"""What each miSRA tool does when its job runs (called from misra.tasks.run_job).

A runner gets the job, its working directory and a log function, writes its result
files into the directory and returns them as [{name, label}].
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.db import connections

from .export import write_tsv

MATRIX_TABLES = {"mgdb": "MirGeneDB", "mirbase": "miRBase", "pmiren": "PmiREN"}
MATRIX_VALUES = {"rc": "read count", "rpm_lib": "RPM (library)", "rpm_total": "RPM (total)"}
CHUNK = 500


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _metadata(job, directory: Path, ids) -> dict:
    write_tsv(ids, directory / "experiments_metadata.tsv")
    return {"name": "experiments_metadata.tsv", "label": "Metadata of the experiments (TSV)"}


def run_matrix(job, directory: Path, log) -> list[dict]:
    table = job.params["db"]
    column = job.params["value"]
    if table not in MATRIX_TABLES or column not in MATRIX_VALUES:  # never interpolate unchecked names
        raise ValueError("unknown reference database or value")
    ids = job.id_list()
    log(f"Expression matrix: {MATRIX_TABLES[table]}, {MATRIX_VALUES[column]}, {len(ids)} experiments")
    frames = []
    with connections["misra"].cursor() as cur:
        for i, chunk in enumerate(_chunks(ids, CHUNK)):
            cur.execute(f"SELECT exp_id, mirna_name, {column} FROM {table} WHERE exp_id IN "
                        f"({', '.join(['%s'] * len(chunk))})", chunk)
            rows = cur.fetchall()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["exp_id", "mirna", "value"]))
            log(f"read {min((i + 1) * CHUNK, len(ids))} of {len(ids)} experiments")
    if not frames:
        raise ValueError(f"None of the experiments has {MATRIX_TABLES[table]} expression values.")
    data = pd.concat(frames, ignore_index=True)
    data["value"] = pd.to_numeric(data["value"], errors="coerce").fillna(0)
    matrix = data.pivot_table(index="mirna", columns="exp_id", values="value", aggfunc="sum", fill_value=0)
    with_values = [e for e in ids if e in matrix.columns]
    missing = [e for e in ids if e not in matrix.columns]
    matrix = matrix[with_values].sort_index()
    name = f"matrix_{table}_{column}.tsv.gz"
    matrix.to_csv(directory / name, sep="\t", compression="gzip", index_label="miRNA")
    log(f"matrix: {matrix.shape[0]} miRNAs x {matrix.shape[1]} experiments")
    results = [{"name": name, "label": f"Expression matrix: {matrix.shape[0]:,} miRNAs × "
                                      f"{matrix.shape[1]:,} experiments ({MATRIX_VALUES[column]}, gzip TSV)"}]
    if missing:
        (directory / "experiments_without_values.txt").write_text("\n".join(missing) + "\n")
        log(f"{len(missing)} experiments have no {MATRIX_TABLES[table]} values (listed in experiments_without_values.txt)")
        results.append({"name": "experiments_without_values.txt",
                        "label": f"{len(missing):,} experiments without {MATRIX_TABLES[table]} values"})
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


def srnatoolbox_command(tool: str) -> tuple[str, dict]:
    """Path of a srnatoolbox command in the conda env, and an environment with the env's bin on PATH."""
    bin_dir = Path(settings.SRNATOOLBOX_ENV) / "bin"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return str(bin_dir / tool), env


def run_download(job, directory: Path, log) -> list[dict]:
    ids = job.id_list()
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
    log("running: " + " ".join(cmd[1:]))
    with open(directory / "tool.log", "w") as out:
        proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=directory,
                              timeout=6 * 3600)
    if proc.returncode != 0:
        tail = (directory / "tool.log").read_text(errors="replace")[-2000:]
        raise RuntimeError(f"sRNAhelper failed (exit {proc.returncode}):\n{tail}")
    name = f"misra_reads.{p['outformat']}"
    if not (directory / name).exists():
        raise RuntimeError(f"sRNAhelper finished but {name} was not written.")
    results = [{"name": name, "label": f"Preprocessed reads of {len(ids) - len(missing):,} experiments"}]
    if missing:
        (directory / "experiments_without_reads.txt").write_text("\n".join(missing) + "\n")
        log(f"{len(missing)} experiments have no read file (listed in experiments_without_reads.txt)")
        results.append({"name": "experiments_without_reads.txt",
                        "label": f"{len(missing):,} experiments without read files"})
    results.append(_metadata(job, directory, [e for e in ids if e not in set(missing)]))
    return results


RUNNERS = {"matrix": run_matrix, "download": run_download}
