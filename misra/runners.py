"""What each miSRA tool does when its job runs (called from jobs.tasks.run_job).

A runner gets the job, its working directory and a log function, writes its result
files into the directory and returns them as [{name, label}].
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.db import connections

from jobs.commands import run_command, srnatoolbox_command

from .export import write_tsv
from .expression import REFERENCE_BY_TABLE, load_values
from .expression import matrix as expression_matrix

MATRIX_TABLES = {"mgdb": "MirGeneDB", "mirbase": "miRBase", "pmiren": "PmiREN"}
MATRIX_VALUES = {"rc": "read count", "rpm_lib": "RPM (library)", "rpm_total": "RPM (total)"}
CHUNK = 500


def _n(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def exp_ids(job) -> list[str]:
    return list(job.inputs.get("exp_ids", []))


def _metadata(job, directory: Path, ids) -> dict:
    write_tsv(ids, directory / "experiments_metadata.tsv")
    return {"name": "experiments_metadata.tsv", "label": "Metadata of the experiments (TSV)"}


def run_matrix(job, directory: Path, log) -> list[dict]:
    table = job.params["db"]
    column = job.params["value"]
    if table not in MATRIX_TABLES or column not in MATRIX_VALUES:  # never interpolate unchecked names
        raise ValueError("unknown reference database or value")
    ids = exp_ids(job)
    log(f"Expression matrix: {MATRIX_TABLES[table]}, {MATRIX_VALUES[column]}, {len(ids)} experiments")
    data = load_values(REFERENCE_BY_TABLE[table], ids, (column,),
                       progress=lambda done, total: log(f"read {done} of {total} experiments"))
    if data.empty:
        raise ValueError(f"None of the experiments has {MATRIX_TABLES[table]} expression values.")
    matrix, missing = expression_matrix(data, column, ids)
    with_values = list(matrix.columns)
    name = f"matrix_{table}_{column}.tsv.gz"
    matrix.to_csv(directory / name, sep="\t", compression="gzip", index_label="miRNA")
    log(f"matrix: {matrix.shape[0]} miRNAs x {matrix.shape[1]} experiments")
    results = [{"name": name, "label": f"Expression matrix: {matrix.shape[0]:,} miRNAs × "
                                      f"{matrix.shape[1]:,} experiments ({MATRIX_VALUES[column]}, gzip TSV)"}]
    if missing:
        (directory / "experiments_without_values.txt").write_text("\n".join(missing) + "\n")
        log(f"{len(missing)} experiments have no {MATRIX_TABLES[table]} values (listed in experiments_without_values.txt)")
        results.append({"name": "experiments_without_values.txt",
                        "label": f"{_n(len(missing), 'experiment')} without {MATRIX_TABLES[table]} values"})
    results.append(_metadata(job, directory, with_values))
    return results


def reads_files(ids) -> dict[str, list[str]]:
    """exp_id -> collapsed read files (paths relative to MISRA_STORAGE)."""
    out: dict[str, list[str]] = {}
    with connections["misra"].cursor() as cur:
        for chunk in _chunks(list(ids), CHUNK):
            cur.execute("SELECT exp_id, relative_path FROM read_files WHERE file_type = 'reads_collapsed' "
                        f"AND exp_id IN ({', '.join(['%s'] * len(chunk))}) ORDER BY exp_id, relative_path", chunk)
            for exp_id, path in cur.fetchall():
                out.setdefault(exp_id, []).append(path)
    return out


def run_download(job, directory: Path, log) -> list[dict]:
    ids = exp_ids(job)
    p = job.params
    files = reads_files(ids)
    storage = Path(settings.MISRA_STORAGE)
    paths, missing = [], []
    for e in ids:
        found = [storage / f for f in files.get(e, []) if (storage / f).exists()]
        if found:
            paths.extend(found)
        else:
            missing.append(e)
    log(f"Download reads: {len(ids)} experiments, {len(paths)} read files found")
    if not paths:
        raise ValueError("No read files are available for these experiments.")
    (directory / "input_files.txt").write_text("".join(f"{x}\n" for x in paths))
    command, env = srnatoolbox_command("sRNAhelper")
    archive = directory / "misra_reads"
    cmd = [command, "prf", "--input", str(directory / "input_files.txt"), "--outfile", str(archive),
           "--outformat", p["outformat"], "--output_sep", p["sep"]]
    for opt, key in (("--min_read_length", "min_len"), ("--max_read_length", "max_len"),
                     ("--min_read_count", "min_rc")):
        if p.get(key) not in (None, ""):
            cmd += [opt, str(p[key])]
    run_command(cmd, directory, env, log, "tool.log", 6)
    name = f"misra_reads.{p['outformat']}"
    if not (directory / name).exists():
        raise RuntimeError(f"sRNAhelper finished but {name} was not written.")
    results = [{"name": name, "label": f"Preprocessed reads of {_n(len(ids) - len(missing), 'experiment')}"}]
    if missing:
        (directory / "experiments_without_reads.txt").write_text("\n".join(missing) + "\n")
        log(f"{len(missing)} experiments have no read file (listed in experiments_without_reads.txt)")
        results.append({"name": "experiments_without_reads.txt",
                        "label": f"{_n(len(missing), 'experiment')} without read files"})
    results.append(_metadata(job, directory, [e for e in ids if e not in set(missing)]))
    return results


# ---------------------------------------------------------------------------
# sRNAbench tools (miRNA profiling, libs, exact matches)

# value columns of the sRNAbench expression files -> matrix file label
BENCH_VALUES = {"RC": "RC", "RCadj": "RCadj", "RPM_lib_adj": "RPM_lib", "RPM_total_adj": "RPM_total"}
KEEP_OUT_OF_ARCHIVE = {"reads.fa", "reads_orig_unfiltered.fa"}  # copies of the input reads


def input_file(job, key: str) -> Path:
    return job.directory() / "input" / f"{key}.fa"


def _expression_file(mode: str, res: dict, sample_dir: Path) -> Path | None:
    """The per-experiment expression table sRNAbench wrote for this mode."""
    if mode == "mirna":
        section = res.get("mirna") or {}
        lib = next(iter(section), None)
        name = ((section.get(lib) or {}).get("files") or {}).get("mature_MA", {}).get("name") if lib else None
    elif mode == "libs":
        section = res.get("libs") or {}
        lib = next(iter(section), None)
        # by file name, independent of the key names in results.json
        return sample_dir / "libs_results" / f"{lib}_expression_sense.tsv" if lib else None
    else:
        name = ((res.get("spike_ins") or {}).get("spike_ins") or {}).get("file")
    if not name:
        return None
    path = Path(name)
    if not path.is_absolute():
        path = sample_dir.parent.parent / path  # paths are relative to the job directory (sRNAbench's cwd)
    return path


def _feature_stats(matrix: pd.DataFrame, path: Path, label: str) -> None:
    stats = pd.DataFrame({
        "experiments_detected": (matrix > 0).sum(axis=1),
        "mean": matrix.mean(axis=1), "median": matrix.median(axis=1), "sum": matrix.sum(axis=1),
        "p25": matrix.quantile(.25, axis=1), "p75": matrix.quantile(.75, axis=1),
        "min": matrix.min(axis=1), "max": matrix.max(axis=1),
    }).sort_values("median", ascending=False)
    stats.to_csv(path, sep="\t", index_label=label, float_format="%.4f")


def run_srnabench(job, directory: Path, log) -> list[dict]:
    mode = job.tool
    p = job.params
    ids = exp_ids(job)
    files = reads_files(ids)
    storage = Path(settings.MISRA_STORAGE)
    rows, missing = [], []
    for e in ids:
        found = [storage / f for f in files.get(e, []) if (storage / f).exists()]
        if found:
            rows.append((found[0], e))
        else:
            missing.append(e)
    log(f"sRNAbench ({mode}): {len(ids)} experiments, {len(rows)} with reads")
    if not rows:
        raise ValueError("No read files are available for these experiments.")
    sheet = directory / "samples.tsv"
    sheet.write_text("file\tname\n" + "".join(f"{f}\t{e}\n" for f, e in rows))

    bench, env = srnatoolbox_command("sRNAbench")
    out = directory / "bench"
    cmd = [bench, "--samples", sheet, "-o", out,
           "--meta_threads", settings.MISRA_SRNABENCH_META_THREADS, "--threads", settings.MISRA_SRNABENCH_THREADS,
           "--silent", "--min_read_count", p.get("min_rc") or 1]
    if p.get("min_len"):
        cmd += ["--min_read_length", p["min_len"]]
    if p.get("max_len"):
        cmd += ["--max_read_length", p["max_len"]]
    if mode == "mirna":
        helper, _ = srnatoolbox_command("sRNAhelper")
        annot = directory / "mirna_annot.tsv"
        run_command([helper, "ma", "--mature", input_file(job, "mature"), "--hairpin", input_file(job, "hairpin"),
              "--outfile", annot], directory, env, log, "tool.log", 1)
        n_hairpins = max(0, len(annot.read_text().splitlines()) - 1) if annot.exists() else 0
        if n_hairpins == 0:
            raise ValueError("None of the mature sequences was found in a hairpin sequence; "
                             "check that the two files belong together.")
        log(f"miRNA annotation: {n_hairpins} hairpins with mature sequences")
        cmd += ["--mirna", annot, "--mm", p.get("mm") or 0, "--notrna", "--nolibs"]
        feature = "miRNA"
    elif mode == "libs":
        cmd += ["--libs", input_file(job, "libs"), "--mm", p.get("mm") or 0, "--nomirna", "--notrna"]
        feature = "reference"
    else:
        cmd += ["--spike_file", input_file(job, "spikes")]
        feature = "sequence"
    run_command(cmd, directory, env, log, "tool.log", settings.MISRA_SRNABENCH_TIMEOUT_HOURS)

    # collect the per-experiment tables
    meta = pd.read_csv(out / "meta_results.csv")
    tables, summary, failed = {}, [], []
    for _, row in meta.iterrows():
        exp_id = str(row["name"])
        res_path = Path(str(row["results"]))
        if not res_path.is_absolute():
            res_path = directory / res_path
        if not res_path.is_file():
            failed.append(exp_id)
            continue
        res = json.loads(res_path.read_text())
        pre = res.get("preproc") or {}
        expr = _expression_file(mode, res, res_path.parent)
        table = pd.read_csv(expr, sep="\t") if expr and expr.is_file() and expr.stat().st_size else None
        assigned = float(table["RCadj"].sum()) if table is not None and "RCadj" in table else 0.0
        summary.append({"experiment": exp_id, "raw_reads": pre.get("raw_reads"), "reads_in_analysis": res.get("rc"),
                        "unique_reads": res.get("ur"), "assigned_reads": assigned,
                        "assigned_percent": round(100 * assigned / res["rc"], 3) if res.get("rc") else None})
        if table is not None and len(table):
            tables[exp_id] = table.set_index(table.columns[0])
    log(f"{len(tables)} experiments with assigned reads, {len(meta) - len(tables)} without")

    results = []
    if tables:
        workbook = {}
        for column, label in BENCH_VALUES.items():
            matrix = pd.DataFrame({e: t[column].groupby(level=0).sum() for e, t in tables.items() if column in t})
            matrix = matrix.fillna(0).sort_index()
            matrix = matrix[[e for _, e in rows if e in matrix.columns]]
            name = f"matrix_{label}.tsv"
            matrix.to_csv(directory / name, sep="\t", index_label=feature)
            results.append({"name": name, "label": f"{label} matrix: {_n(matrix.shape[0], feature)} × "
                                                    f"{_n(matrix.shape[1], 'experiment')}"})
            workbook[label] = matrix
        _feature_stats(workbook["RPM_total"], directory / "feature_stats_RPM_total.tsv", feature)
        results.append({"name": "feature_stats_RPM_total.tsv",
                        "label": f"Statistics per {feature} across experiments (RPM total)"})
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(directory / "experiment_summary.tsv", sep="\t", index=False)
    results.append({"name": "experiment_summary.tsv", "label": "Reads in analysis and assigned reads per experiment"})
    if tables:
        with pd.ExcelWriter(directory / "results.xlsx") as xl:
            summary_df.to_excel(xl, sheet_name="summary", index=False)
            for label, matrix in workbook.items():
                matrix.to_excel(xl, sheet_name=label, index_label=feature)
        results.append({"name": "results.xlsx", "label": "Summary and all matrices (Excel)"})

    # per-experiment sRNAbench output, without the copies of the input reads
    with zipfile.ZipFile(directory / "srnabench_output.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out.rglob("*")):
            if path.is_file() and path.name not in KEEP_OUT_OF_ARCHIVE:
                zf.write(path, path.relative_to(out))
    results.append({"name": "srnabench_output.zip", "label": "Full sRNAbench output per experiment (ZIP)"})
    shutil.rmtree(out, ignore_errors=True)  # the archive keeps everything needed

    no_reads = missing + failed
    if no_reads:
        (directory / "experiments_without_reads.txt").write_text("\n".join(no_reads) + "\n")
        results.append({"name": "experiments_without_reads.txt",
                        "label": f"{_n(len(no_reads), 'experiment')} without reads or sRNAbench results"})
    results.append(_metadata(job, directory, [e for _, e in rows]))
    if not tables:
        log("no reads were assigned to the reference sequences in any experiment")
    return results


RUNNERS = {"matrix": run_matrix, "download": run_download,
           "mirna": run_srnabench, "libs": run_srnabench, "exact": run_srnabench}
