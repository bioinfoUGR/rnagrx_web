"""Summary of a finished sRNAbench job, for the job page, the sample pages and the downloads.

sRNAbench (--samples) writes one directory per sample under <job>/bench/ with results.json; paths inside are
relative to the job directory (sRNAbench's working directory). build_summary() runs at the end of the job and
writes summary.json (per-sample numbers, RNA classes, read lengths, top microRNAs) and the microRNA matrices.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import pandas as pd

BENCH_DIR = "bench"
SUMMARY = "summary.json"
VALUES = {"RC": "read count", "RCadj": "adjusted read count", "RPM_lib_adj": "RPM (microRNA reads)",
          "RPM_total_adj": "RPM (reads in analysis)"}
TOP_MIRNAS = 25
SAMPLE_TOP = 50
CLASS_ORDER = ["microRNA", "tRNA", "rRNA", "snoRNA", "snRNA", "Y RNA", "other small ncRNA", "lncRNA",
               "mRNA, introns, other genomic", "other", "unassigned"]
CLASS_OF = {"tRNA": "tRNA", "mt_tRNA": "tRNA", "rRNA": "rRNA", "mt_rRNA": "rRNA", "snoRNA": "snoRNA",
            "snRNA": "snRNA", "Y_RNA": "Y RNA", "scRNA": "other small ncRNA", "vault_RNA": "other small ncRNA",
            "ncRNA": "other small ncRNA", "RNase_MRP_RNA": "other small ncRNA", "RNase_P_RNA": "other small ncRNA",
            "misc_RNA": "other small ncRNA", "piRNA": "other small ncRNA", "telomerase_RNA": "other small ncRNA",
            "lnc_RNA": "lncRNA", "antisense_RNA": "lncRNA", "mRNA": "mRNA, introns, other genomic",
            "intron": "mRNA, introns, other genomic", "TSS": "mRNA, introns, other genomic",
            "TES": "mRNA, introns, other genomic", "pseudogene": "mRNA, introns, other genomic",
            "regulatory": "mRNA, introns, other genomic", "Region": "mRNA, introns, other genomic",
            "misc_recomb": "mRNA, introns, other genomic", "unassigned": "unassigned", "other_rna": "other"}
PREPROC_STEPS = [("raw_reads", "Raw reads"), ("adapter_dimer_reads", "Adapter dimers"),
                 ("ultra_short_reads", "Too short after trimming"), ("length_filtered_reads", "Outside the length range"),
                 ("low_complexity_filtered", "Low complexity"), ("quality_filtered_reads", "Low quality"),
                 ("rc_filtered_reads", "Below the min. read count"), ("umi_filtered_reads", "UMI duplicates"),
                 ("reads", "Reads in analysis")]


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(v) else v


def job_path(directory: Path, name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(name)
    return p if p.is_absolute() else directory / p


def results_json(directory: Path, sample: str) -> dict | None:
    path = directory / BENCH_DIR / sample / "results.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def rna_class(rna_type: str, mirna_keys: set[str]) -> str:
    base = rna_type.rsplit("_", 1)[0] if rna_type.endswith(("_+", "_-")) else rna_type
    if base.replace("-", "_") in mirna_keys:
        return "microRNA"
    return CLASS_OF.get(base, "other")


def _classes(directory: Path, res: dict) -> dict[str, float]:
    """Percent of reads in analysis per RNA class (both strands)."""
    files = (res.get("postproc") or {}).get("files") or {}
    path = job_path(directory, (files.get("mapping_stat_total") or {}).get("name"))
    out: dict[str, float] = {}
    if not path or not path.is_file():
        return out
    keys = set(res.get("mirna") or {})
    for _, row in pd.read_csv(path, sep="\t").iterrows():
        cls = rna_class(str(row["rna_type"]), keys)
        out[cls] = out.get(cls, 0.0) + _num(row.get("rc_perc"))
    return {k: round(v, 3) for k, v in out.items() if v > 0}


def _lengths(directory: Path, res: dict) -> dict[int, float]:
    """Percent of reads in analysis per read length."""
    files = (res.get("preproc") or {}).get("files") or {}
    path = job_path(directory, (files.get("read_length") or {}).get("name"))
    if not path or not path.is_file():
        return {}
    df = pd.read_csv(path, sep="\t")
    col = "rc_perc" if "rc_perc" in df else df.columns[3]
    return {int(r.iloc[0]): round(_num(r[col]), 3) for _, r in df.iterrows() if _num(r[col]) > 0}


def mirna_table(directory: Path, res: dict) -> pd.DataFrame | None:
    """Mature microRNA expression of a sample (all microRNA references), indexed by name."""
    tables = []
    for section in (res.get("mirna") or {}).values():
        name = (((section or {}).get("files") or {}).get("mature_MA") or {}).get("name")
        path = job_path(directory, name)
        if path and path.is_file() and path.stat().st_size:
            tables.append(pd.read_csv(path, sep="\t"))
    if not tables:
        return None
    df = pd.concat(tables)
    df = df.set_index(df.columns[0])
    return df


def sample_stats(directory: Path, name: str, res: dict) -> dict:
    pre = res.get("preproc") or {}
    reads = _num(res.get("rc"))
    mirna_reads = sum(_num(m.get("rcadj_mature", m.get("rcadj"))) for m in (res.get("mirna") or {}).values())
    trna_reads = sum(_num(t.get("rcadj")) for t in (res.get("trna") or {}).values())
    table = mirna_table(directory, res)
    detected = int((table["RC"] > 0).groupby(level=0).any().sum()) if table is not None and "RC" in table else 0
    return {
        "name": name, "protocol": (res.get("parameters") or {}).get("protocol", ""),
        "raw": _num(pre.get("raw_reads")), "adapter_dimers": _num(pre.get("adapter_dimer_reads")),
        "too_short": _num(pre.get("ultra_short_reads")) + _num(pre.get("length_filtered_reads")),
        "filtered": sum(_num(pre.get(k)) for k in ("low_complexity_filtered", "quality_filtered_reads",
                                                    "rc_filtered_reads", "umi_filtered_reads",
                                                    "adapter_filtered_reads", "non_concordant_reads")),
        "reads": reads, "unique": _num(res.get("ur")),
        "mirna_reads": mirna_reads, "mirna_pct": round(100 * mirna_reads / reads, 2) if reads else 0.0,
        "trna_reads": trna_reads, "mirnas": detected,
        "classes": _classes(directory, res), "lengths": _lengths(directory, res),
        "preproc": [(label, _num(pre.get(key))) for key, label in PREPROC_STEPS if key in pre],
    }


def _failure(directory: Path, name: str) -> str:
    log = directory / BENCH_DIR / name / "log.txt"
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return "sRNAbench produced no results for this sample."
    errors = [l.split("] ", 1)[-1] for l in lines if "[ERROR]" in l or "[CRITICAL]" in l]
    return (errors[-1] if errors else "sRNAbench produced no results for this sample.")[:300]


def _flag(stats: list[dict]) -> None:
    """Mark samples far below the others in reads in analysis or microRNA share."""
    if len(stats) < 3:
        return
    med_reads = statistics.median(s["reads"] for s in stats)
    med_mirna = statistics.median(s["mirna_pct"] for s in stats)
    for s in stats:
        notes = []
        if med_reads and s["reads"] < 0.2 * med_reads:
            notes.append("few reads")
        if med_mirna and s["mirna_pct"] < med_mirna / 3:
            notes.append("low microRNA share")
        s["flags"] = notes


def build_summary(job, directory: Path, log) -> list[dict]:
    samples = job.inputs["samples"]
    columns = job.inputs.get("columns", [])
    group_col = columns[0] if columns else ""
    stats, failed, tables = [], [], {}
    for s in samples:
        res = results_json(directory, s["name"])
        if res is None:
            failed.append({"name": s["name"], "reason": _failure(directory, s["name"])})
            continue
        st = sample_stats(directory, s["name"], res)
        st["group"] = s.get("annotations", {}).get(group_col, "") if group_col else ""
        stats.append(st)
        table = mirna_table(directory, res)
        if table is not None:
            tables[s["name"]] = table
    log(f"{len(stats)} samples profiled, {len(failed)} without results")
    if not stats:
        raise ValueError("sRNAbench produced no results for any sample. "
                         + (failed[0]["reason"] if failed else ""))
    _flag(stats)

    results = []
    matrices = {}
    names = [s["name"] for s in stats if s["name"] in tables]
    for col, label in VALUES.items():
        m = pd.DataFrame({n: tables[n][col].groupby(level=0).sum() for n in names if col in tables[n]})
        if m.empty:
            continue
        m = m.fillna(0).sort_index()
        matrices[col] = m
        fname = f"mirna_{col}.tsv"
        m.to_csv(directory / fname, sep="\t", index_label="microRNA", float_format="%.4g")
        results.append({"name": fname, "label": f"microRNA expression, {label}: {m.shape[0]:,} microRNAs × "
                                                 f"{m.shape[1]:,} samples"})
    table_rows = [{"sample": s["name"], **({group_col: s["group"]} if group_col else {}), "raw_reads": s["raw"],
                   "adapter_dimers": s["adapter_dimers"], "too_short": s["too_short"], "filtered": s["filtered"],
                   "reads_in_analysis": s["reads"], "unique_reads": s["unique"], "mirna_reads": s["mirna_reads"],
                   "mirna_percent": s["mirna_pct"], "mirnas_detected": s["mirnas"],
                   **{f"{c} (%)": s["classes"].get(c, 0.0) for c in CLASS_ORDER}} for s in stats]
    summary_df = pd.DataFrame(table_rows)
    summary_df.to_csv(directory / "sample_summary.tsv", sep="\t", index=False)
    results.append({"name": "sample_summary.tsv", "label": "Reads and RNA classes per sample"})
    with pd.ExcelWriter(directory / "results.xlsx") as xl:
        summary_df.to_excel(xl, sheet_name="samples", index=False)
        for col, m in matrices.items():
            m.to_excel(xl, sheet_name=col[:31], index_label="microRNA")
    results.append({"name": "results.xlsx", "label": "Sample summary and microRNA matrices (Excel)"})
    if failed:
        (directory / "samples_failed.tsv").write_text(
            "sample\treason\n" + "".join(f"{f['name']}\t{f['reason']}\n" for f in failed))
        results.append({"name": "samples_failed.tsv", "label": f"{len(failed)} samples without results"})

    top = {"groups": [], "rows": []}
    if "RPM_total_adj" in matrices:
        rpm = matrices["RPM_total_adj"]
        groups = sorted({s["group"] for s in stats if s["group"]}) if group_col else []
        by_group = {g: [s["name"] for s in stats if s["group"] == g and s["name"] in rpm] for g in groups}
        mean = rpm.mean(axis=1).sort_values(ascending=False)
        top["groups"] = groups
        for mir in mean.index[:TOP_MIRNAS]:
            row = rpm.loc[mir]
            top["rows"].append({"name": mir, "mean": round(float(mean[mir]), 2),
                                "groups": [round(float(row[by_group[g]].mean()), 2) if by_group[g] else None
                                           for g in groups],
                                "detected": int((matrices["RC"].loc[mir] > 0).sum()) if "RC" in matrices else None})
    present = [c for c in CLASS_ORDER if any(c in s["classes"] for s in stats)]
    all_mirnas = int((matrices["RC"] > 0).any(axis=1).sum()) if "RC" in matrices else 0
    total_raw = sum(s["raw"] for s in stats)
    total_reads = sum(s["reads"] for s in stats)
    summary = {
        "samples": stats, "failed": failed, "group_column": group_col, "classes": present, "top": top,
        "totals": {"raw": total_raw, "reads": total_reads,
                   "reads_pct": round(100 * total_reads / total_raw, 1) if total_raw else 0,
                   "mirna_pct": round(statistics.mean(s["mirna_pct"] for s in stats), 1),
                   "mirnas": all_mirnas, "protocols": sorted({s["protocol"] for s in stats if s["protocol"]})},
    }
    (directory / SUMMARY).write_text(json.dumps(summary))
    return results


def load_summary(job) -> dict | None:
    try:
        return json.loads((job.directory() / SUMMARY).read_text())
    except (OSError, ValueError):
        return None


def progress(job) -> dict:
    names = [s["name"] for s in job.inputs.get("samples", [])]
    bench = job.directory() / BENCH_DIR
    done = sum(1 for n in names if (bench / n / "results.json").is_file())
    return {"done": done, "total": len(names), "pct": int(100 * done / len(names)) if names else 0}


def sample_report(job, name: str) -> dict | None:
    """Everything the sample page shows: numbers, microRNAs, tRNAs, other classes, files."""
    directory = job.directory()
    res = results_json(directory, name)
    if res is None:
        return None
    stats = sample_stats(directory, name, res)
    table = mirna_table(directory, res)
    mirnas = []
    if table is not None and len(table):
        t = table.sort_values("RPM_total_adj" if "RPM_total_adj" in table else "RC", ascending=False)
        for mir, r in t.head(SAMPLE_TOP).iterrows():
            mirnas.append({"name": mir, "ur": _num(r.get("UR")), "rc": _num(r.get("RC")), "rcadj": _num(r.get("RCadj")),
                           "rpm": _num(r.get("RPM_total_adj")),
                           "precursor": str(r.get("preMiRNA string", "")).split(",")[0]})
    trnas = []
    for section in (res.get("trna") or {}).values():
        path = job_path(directory, (((section or {}).get("files") or {}).get("senseMA") or {}).get("name"))
        if path and path.is_file() and path.stat().st_size:
            df = pd.read_csv(path, sep="\t")
            for _, r in df.sort_values("RCadj", ascending=False).head(20).iterrows():
                trnas.append({"name": r.iloc[0], "rcadj": _num(r["RCadj"]), "rpm": _num(r.get("RPM_total_adj"))})
    trnas.sort(key=lambda x: -x["rcadj"])
    libs = []
    for key, section in (res.get("libs") or {}).items():
        ref = (section or {}).get("reference") or {}
        libs.append({"name": ref.get("short_desc") or ref.get("name") or key, "reads": _num(section.get("rcadj")),
                     "sequences": int(_num(section.get("seq_count")))})
    libs.sort(key=lambda x: -x["reads"])
    sample_dir = directory / BENCH_DIR / name
    files = sorted((str(p.relative_to(sample_dir)), p.stat().st_size) for p in sample_dir.rglob("*") if p.is_file())
    return {"stats": stats, "mirnas": mirnas, "trnas": trnas[:20], "libs": libs, "files": files,
            "classes": [c for c in CLASS_ORDER if c in stats["classes"]]}
