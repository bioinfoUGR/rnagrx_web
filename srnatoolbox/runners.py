"""What the sRNAtoolbox tools do when their job runs (called from jobs.tasks.run_job)."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pandas as pd
from django.conf import settings

from jobs.commands import run_command, srnatoolbox_command
from jobs.models import DataFile, Dataset

from .de import DE_DIR, build_de_summary, parent_of
from .summary import build_summary

INPUT_DIR = "input"
BENCH_DIR = "bench"
# copies of the input reads, temporary files and raw alignments that sRNAbench leaves in each sample directory
# (charts are drawn by the web pages, so sRNAbench runs without --graphics)
REMOVE_FROM_SAMPLES = {"reads.fa", "reads_orig_unfiltered.fa"}
REMOVE_DIRS = {"chunks"}
REMOVE_SUFFIXES = (".bowtie", ".ebwt")


def input_file(job, key: str) -> Path:
    return job.directory() / INPUT_DIR / f"{key}.fa"


def sample_files(job) -> list[tuple[dict, list[Path]]]:
    """(sample, file paths) of the job's samples; ValueError if the dataset or a file is gone."""
    ds = Dataset.objects.filter(slug=job.inputs.get("dataset", "")).first()
    if ds is None or ds.deleted_at:
        raise ValueError("The dataset of this job was deleted, so its read files are not available any more.")
    files = {f.tag: f for f in ds.files.filter(status=DataFile.READY)}
    out = []
    for s in job.inputs["samples"]:
        paths = [files[t].path() for t in s["files"] if t in files]
        if len(paths) != len(s["files"]) or not all(p.is_file() for p in paths):
            raise ValueError(f"Read files of sample {s['name']} were removed from the dataset.")
        out.append((s, paths))
    return out


def write_sheet(job, directory: Path) -> Path:
    columns = job.inputs.get("columns", [])
    sheet = directory / "samples.tsv"
    with open(sheet, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        w.writerow(["file", "name"] + columns)
        for s, paths in sample_files(job):
            w.writerow([":".join(str(p) for p in paths), s["name"]] +
                       [s.get("annotations", {}).get(c, "") for c in columns])
    return sheet


def srnabench_command(job, directory: Path, log, env) -> list:
    p = job.params
    bench, _ = srnatoolbox_command("sRNAbench")
    cmd = [bench, "--samples", "samples.tsv", "--samples_sep", "\t", "-o", BENCH_DIR,
           "--dbpath", settings.SRNATOOLBOX_DB,
           "--meta_threads", settings.SRNATOOLBOX_META_THREADS, "--threads", settings.SRNATOOLBOX_THREADS,
           "--silent",
           "--min_read_count", p["min_rc"], "--min_read_length", p["min_len"], "--mm", p["mm"],
           "--adapter_mm", p["adapter_mm"], "--min_adapter_length", p["min_adapter_length"]]
    if p.get("max_len"):
        cmd += ["--max_read_length", p["max_len"]]
    cmd += ["--accession", p["accession"]] if p.get("accession") else ["--taxon_id", p["taxon"]]
    if p["mirdb"] == "own":
        helper, _ = srnatoolbox_command("sRNAhelper")
        annot = directory / "mirna_annot.tsv"
        run_command([helper, "ma", "--mature", input_file(job, "mature"), "--hairpin", input_file(job, "hairpin"),
                     "--outfile", annot], directory, env, log, "srnabench.log", 1)
        n = max(0, len(annot.read_text().splitlines()) - 1) if annot.exists() else 0
        if n == 0:
            raise ValueError("None of your mature microRNA sequences was found in a hairpin sequence; "
                             "check that the two files belong together.")
        log(f"own microRNA reference: {n} hairpins with mature sequences")
        cmd += ["--mirna", annot]
    else:
        cmd += ["--mirdb", p["mirdb"]]
    protocol = p.get("protocol", "guess")
    if protocol == "custom":
        cmd += ["--adapter_sequence", p["adapter"], "--random_5p", p.get("random_5p") or 0,
                "--random_3p", p.get("random_3p") or 0]
    elif protocol != "trimmed":
        cmd += ["--protocol", protocol]
    if p.get("libs_file"):
        cmd += ["--libs", input_file(job, "libs")]
    if p.get("spikes_file"):
        cmd += ["--spike_file", input_file(job, "spikes")]
    if not p.get("low_complexity", True):
        cmd.append("--no_filter_low_complexity")
    if p.get("seed_expression"):
        cmd.append("--calc_seed_expression")
    return cmd


def tidy(out: Path) -> None:
    """Remove the copies of the input reads and temporary files from the sample directories."""
    if not out.is_dir():
        return
    for sample_dir in out.iterdir():
        if not sample_dir.is_dir():
            continue
        for name in REMOVE_FROM_SAMPLES:
            (sample_dir / name).unlink(missing_ok=True)
        for name in REMOVE_DIRS:
            shutil.rmtree(sample_dir / name, ignore_errors=True)
        for path in sample_dir.rglob("*"):
            if path.is_file() and path.name.endswith(REMOVE_SUFFIXES):
                path.unlink()


def run_srnabench(job, directory: Path, log) -> list[dict]:
    write_sheet(job, directory)
    log(f"sRNAbench on {job.n_items} samples of dataset {job.inputs.get('dataset')}")
    _, env = srnatoolbox_command("sRNAbench")
    cmd = srnabench_command(job, directory, log, env)
    try:
        run_command(cmd, directory, env, log, "srnabench.log", settings.SRNATOOLBOX_TIMEOUT_HOURS)
    finally:
        tidy(directory / BENCH_DIR)
    return build_summary(job, directory, log)


# ---------------------------------------------------------------------------
# sRNAde

META_COLUMNS = ["file", "name", "filepath", "results", "parameters"]


def write_de_input(job, parent, directory: Path) -> Path:
    """The parent's meta_results.csv, reduced to the job's samples and its one annotation column."""
    column = job.params["column"]
    groups = {s["name"]: s["group"] for s in job.inputs["samples"]}
    meta = pd.read_csv(parent.directory() / BENCH_DIR / "meta_results.csv", dtype=str, keep_default_na=False)
    meta = meta[meta["name"].isin(groups)]
    missing = set(groups) - set(meta["name"])
    if missing:
        raise ValueError(f"No sRNAbench results for {', '.join(sorted(missing))}.")
    out = meta[[c for c in META_COLUMNS if c in meta.columns]].copy()
    out[column] = out["name"].map(groups)
    target = directory / "de_input.csv"
    out.to_csv(target, index=False)
    return target


def srnade_command(job) -> list:
    p = job.params
    de, _ = srnatoolbox_command("sRNAde")
    cmd = [de, "diffexpr", "-i", "de_input.csv", "-o", DE_DIR, "--silent", "--graphics_no",
           "--library_type", p["library_type"], "--annotation", p["column"], "--de_type", p["count_type"],
           "--filter_mode", p["filter_mode"], "--min_rc", p["min_rc"], "--min_rpm", p["min_rpm"],
           "--fdr_threshold", p["fdr"], "--log2fc_threshold", p["log2fc"]]
    if p["method"] != "all":
        cmd += ["--de_method", p["method"]]
    return cmd


def run_srnade(job, directory: Path, log) -> list[dict]:
    parent = parent_of(job)
    counts = {}
    for s in job.inputs["samples"]:
        counts[s["group"]] = counts.get(s["group"], 0) + 1
    log(f"sRNAde on {job.n_items} samples of sRNAbench job {parent.slug}: "
        + ", ".join(f"{g} {n}" for g, n in sorted(counts.items())))
    write_de_input(job, parent, directory)
    link = directory / BENCH_DIR
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(parent.directory() / BENCH_DIR, target_is_directory=True)
    _, env = srnatoolbox_command("sRNAde")
    try:
        run_command(srnade_command(job), directory, env, log, "srnade.log", settings.SRNATOOLBOX_TIMEOUT_HOURS)
    finally:
        link.unlink(missing_ok=True)
    return build_de_summary(job, directory, log)
