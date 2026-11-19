"""sRNAde on a finished sRNAbench job: the parent's samples for the form, PCA, and the job's result summary.

The sRNAde job (Job.tool "srnade", Job.parent = the sRNAbench job) reads the parent's per-sample results through
a link <job>/bench -> <parent>/bench (the paths inside sRNAbench's results.json are relative to the job
directory) and writes its own output to <job>/de/. The link is removed when the job ends.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import numpy as np
import pandas as pd

from jobs.models import Dataset, Job

from .summary import BENCH_DIR, load_summary, results_json

DE_DIR = "de"
DE_SUMMARY = "de_summary.json"
PCA_MATRIX = "mirna_RPM_total_adj.tsv"
PCA_FEATURES = 500
FAR_FACTOR = 3.0  # distance to the group centre (PCA) above FAR_FACTOR x the group's median distance
VOLCANO_MAX = 4000  # points per volcano plot (the most significant ones)
TABLE_MAX = 500  # rows per comparison in the page's table (the full table is a download)
HEATMAP_ROWS = 40


class Parent:
    """What an sRNAde form needs from a finished sRNAbench job."""

    def __init__(self, job: Job):
        self.job = job
        self.summary = load_summary(job) or {}
        # annotations: the dataset's current sample sheet (annotating after profiling is fine), else the
        # snapshot taken when the sRNAbench job was submitted
        self.columns: list[str] = list(job.inputs.get("columns", []))
        current: dict[str, dict] = {}
        self.dataset = Dataset.objects.filter(slug=job.inputs.get("dataset", ""), deleted_at__isnull=True).first()
        if self.dataset is not None:
            self.columns = list(self.dataset.columns)
            current = dict(self.dataset.samples.values_list("name", "annotations"))
        stats = {s["name"]: s for s in self.summary.get("samples", [])}
        self.failed = [f["name"] for f in self.summary.get("failed", [])]
        self.samples = []
        for s in job.inputs.get("samples", []):
            st = stats.get(s["name"])
            if st is None:
                continue
            annotations = current.get(s["name"], s.get("annotations", {})) or {}
            self.samples.append({"name": s["name"], "annotations": annotations, "reads": st["reads"],
                                 "mirna_pct": st["mirna_pct"], "flags": list(st.get("flags", []))})
        self.library_types = self._library_types()

    @property
    def usable(self) -> bool:
        return len(self.samples) >= 2 and bool(self.columns)

    def groups(self, column: str) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for s in self.samples:
            g = s["annotations"].get(column, "")
            if g:
                counts[g] = counts.get(g, 0) + 1
        return sorted(counts.items())

    def _library_types(self) -> list[str]:
        found = ["mirna"]
        directory = self.job.directory()
        for s in self.samples[:5]:
            res = results_json(directory, s["name"]) or {}
            for key in ("trna", "libs"):
                if res.get(key) and key not in found:
                    found.append(key)
        return [k for k in ("mirna", "trna", "libs") if k in found]


def parent_of(job: Job) -> Job:
    """The finished sRNAbench job an sRNAde job runs on; ValueError when it is gone."""
    parent = job.parent
    if parent is None or parent.status != Job.DONE or not (parent.directory() / BENCH_DIR).is_dir():
        raise ValueError("The results of the sRNAbench job were deleted, so sRNAde cannot use them any more.")
    return parent


# ---------------------------------------------------------------------------
# PCA of the parent's microRNA expression

def _log_rpm(job: Job) -> pd.DataFrame | None:
    try:
        m = pd.read_csv(job.directory() / PCA_MATRIX, sep="\t", index_col=0)
    except (OSError, ValueError):
        return None
    return np.log2(m.clip(lower=0) + 1)


def pca(job: Job, selected: list[str]) -> dict | None:
    """PCA fitted on the selected samples (top variable microRNAs), all samples projected onto it."""
    m = _log_rpm(job)
    if m is None:
        return None
    selected = [n for n in selected if n in m.columns]
    if len(selected) < 3:
        return None
    sub = m[selected]
    sub = sub[(sub > 0).any(axis=1)]
    var = sub.var(axis=1).sort_values(ascending=False)
    features = var.index[:PCA_FEATURES]
    if len(features) < 2:
        return None
    x = sub.loc[features].T.to_numpy(dtype=float)  # samples x features
    centre = x.mean(axis=0)
    _, s, vt = np.linalg.svd(x - centre, full_matrices=False)
    explained = (s ** 2) / max(float((s ** 2).sum()), 1e-12)
    comps = vt[:2]
    allx = m.reindex(features).fillna(0).T.to_numpy(dtype=float)
    scores = (allx - centre) @ comps.T
    names = list(m.columns)
    return {"names": names, "x": [round(float(v), 4) for v in scores[:, 0]],
            "y": [round(float(v), 4) for v in scores[:, 1]] if scores.shape[1] > 1 else [0.0] * len(names),
            "explained": [round(100 * float(v), 1) for v in explained[:2]], "features": len(features)}


def far_from_group(job: Job, parent: Parent, column: str) -> set[str]:
    """Samples whose PCA position (all samples) is far from the centre of their group."""
    names = [s["name"] for s in parent.samples]
    res = pca(job, names)
    if res is None or not column:
        return set()
    pos = dict(zip(res["names"], zip(res["x"], res["y"])))
    by_group: dict[str, list[str]] = {}
    for s in parent.samples:
        g = s["annotations"].get(column, "")
        if g and s["name"] in pos:
            by_group.setdefault(g, []).append(s["name"])
    far = set()
    for members in by_group.values():
        if len(members) < 4:
            continue
        cx = statistics.median(pos[n][0] for n in members)
        cy = statistics.median(pos[n][1] for n in members)
        dist = {n: math.hypot(pos[n][0] - cx, pos[n][1] - cy) for n in members}
        med = statistics.median(dist.values())
        if med > 0:
            far |= {n for n, d in dist.items() if d > FAR_FACTOR * med}
    return far


# ---------------------------------------------------------------------------
# result summary of an sRNAde job (de_summary.json), read by the job page

LFC_COLS = ("log2FoldChange", "logFC", "log2FC")
P_COLS = ("pvalue", "PValue", "P.Value")
PADJ_COLS = ("padj", "FDR", "adj.P.Val")
LIBRARY_ORDER = {"mirna": 0, "trna": 1, "libs": 2}


def _rel(directory: Path, path) -> str | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = directory / p
    try:
        return str(p.resolve().relative_to(directory.resolve()))
    except ValueError:
        return None


def _pick(columns, names) -> str | None:
    for n in names:
        if n in columns:
            return n
    return None


def _walk(node, trail: list[str]):
    """(trail, de_results) for every 'de_results' dict in sRNAde's results.json."""
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key == "de_results" and isinstance(value, dict):
            yield trail, value
        else:
            yield from _walk(value, trail + [key])


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def read_de_table(path: Path) -> pd.DataFrame | None:
    """A DE result table with standard columns gene, mean_a, mean_b, lfc, p, padj."""
    try:
        df = pd.read_csv(path, sep="\t")
    except (OSError, ValueError):
        return None
    if df.empty:
        return None
    lfc, p, padj = _pick(df.columns, LFC_COLS), _pick(df.columns, P_COLS), _pick(df.columns, PADJ_COLS)
    if lfc is None:
        return None
    means = [c for c in df.columns if c.startswith("mean_")]
    out = pd.DataFrame({"gene": df[df.columns[0]].astype(str), "lfc": pd.to_numeric(df[lfc], errors="coerce")})
    out["p"] = pd.to_numeric(df[p], errors="coerce") if p else float("nan")
    out["padj"] = pd.to_numeric(df[padj], errors="coerce") if padj else out["p"]
    out["mean_a"] = pd.to_numeric(df[means[0]], errors="coerce") if len(means) > 0 else float("nan")
    out["mean_b"] = pd.to_numeric(df[means[1]], errors="coerce") if len(means) > 1 else float("nan")
    out.attrs["mean_cols"] = [c[5:] for c in means[:2]]
    return out.sort_values(["padj", "p"], na_position="last", kind="stable")


def _cpm(counts_path: Path) -> pd.DataFrame | None:
    try:
        m = pd.read_csv(counts_path, sep="\t", index_col=0)
    except (OSError, ValueError):
        return None
    m = m.apply(pd.to_numeric, errors="coerce").fillna(0)
    totals = m.sum(axis=0).replace(0, 1)
    return np.log2(m / totals * 1e6 + 1)


def _heatmap(cpm: pd.DataFrame, genes: list[str], samples: list[tuple[str, str]]) -> dict | None:
    names = [n for n, _ in samples if n in cpm.columns]
    genes = [g for g in genes if g in cpm.index]
    if len(genes) < 2 or len(names) < 2:
        return None
    sub = cpm.loc[genes, names]
    sd = sub.std(axis=1).replace(0, 1)
    z = sub.sub(sub.mean(axis=1), axis=0).div(sd, axis=0).clip(-3, 3)
    group = dict(samples)
    return {"genes": genes, "samples": names, "groups": [group[n] for n in names],
            "z": [[round(float(v), 2) for v in row] for row in z.to_numpy()]}


def _short(reference: str) -> str:
    """GCF_000001405.40_canon_lncRNA -> lncRNA; Hsa_mgdb stays."""
    if "_canon_" in reference:
        return reference.split("_canon_", 1)[1]
    return reference


def build_de_summary(job, directory: Path, log) -> list[dict]:
    p = job.params
    fdr, min_lfc = float(p["fdr"]), float(p["log2fc"])
    try:
        res = json.loads((directory / DE_DIR / "results.json").read_text())
    except (OSError, ValueError):
        raise ValueError("sRNAde wrote no results; see srnade.log in the job's files.")
    samples = [(s["name"], s["group"]) for s in job.inputs["samples"]]
    analyses, cpm_cache = [], {}
    sheets: list[tuple[str, pd.DataFrame]] = []
    for trail, methods in _walk(res, []):
        # trail: library, reference, expression[_sense|_antisense], count type, annotation (sRNAde 2026)
        library = trail[0] if trail else ""
        reference = trail[1] if len(trail) > 1 else ""
        strand = next((t.split("_", 1)[1] for t in trail if t.startswith("expression_")), "")
        count_type = next((t for t in trail if t in ("rcadj", "rcsa")), "")
        for method, info in methods.items():
            files = (info or {}).get("files") or {}
            counts_rel = _rel(directory, files.get("counts"))
            for comparison, path in (files.get("pairwise") or {}).items():
                rel = _rel(directory, path)
                table = read_de_table(directory / rel) if rel else None
                if table is None:
                    continue
                has_p = bool(table["p"].notna().any()) and not bool((table["p"].fillna(1) >= 1).all())
                sig = table[(table["padj"] < fdr) & (table["lfc"].abs() >= min_lfc)] if has_p \
                    else table.iloc[0:0]
                a, b = (comparison.split("_vs_", 1) + [""])[:2]
                vol = table.dropna(subset=["lfc"]).head(VOLCANO_MAX)
                sig_set = set(sig["gene"])
                analyses.append({
                    "id": f"a{len(analyses)}", "library": library, "reference": reference, "strand": strand,
                    "count_type": count_type, "method": method, "comparison": comparison, "a": a, "b": b, "file": rel, "counts": counts_rel,
                    "tested": int(len(table)), "stats": has_p,
                    "up": int((sig["lfc"] > 0).sum()), "down": int((sig["lfc"] < 0).sum()),
                    "significant": sig["gene"].tolist(),
                    "rows": [[r.gene, _finite(r.mean_a), _finite(r.mean_b), _finite(r.lfc), _finite(r.p),
                              _finite(r.padj)] for r in table.head(TABLE_MAX).itertuples()],
                    "mean_cols": table.attrs.get("mean_cols", [a, b]),
                    "volcano": {"g": vol["gene"].tolist(), "x": [round(float(v), 3) for v in vol["lfc"]],
                                "y": [round(-math.log10(max(float(v), 1e-300)), 3) if _finite(v) is not None
                                      else 0.0 for v in vol["p"]],
                                "s": [(1 if x > 0 else -1) if g in sig_set else 0
                                      for g, x in zip(vol["gene"], vol["lfc"])]},
                })
                sheets.append((" ".join(x for x in (method, comparison, _short(reference), strand) if x), table))
    if not analyses:
        raise ValueError("sRNAde produced no comparison; see srnade.log in the job's files.")
    analyses.sort(key=lambda x: (LIBRARY_ORDER.get(x["library"], 9), x["reference"], x["strand"] != "sense",
                                 x["strand"], x["count_type"], x["comparison"], x["method"]))

    # consensus and heatmap per comparison (library, reference, count type, comparison)
    comparisons = []
    keyed: dict[tuple, list[dict]] = {}
    for an in analyses:
        keyed.setdefault((an["library"], an["reference"], an["strand"], an["count_type"], an["comparison"]),
                         []).append(an)
    for key, items in keyed.items():
        library, reference, strand, count_type, comparison = key
        sets = [set(i["significant"]) for i in items if i["stats"]]
        consensus = sorted(set.intersection(*sets)) if len(sets) > 1 else []
        first = items[0]
        heat = None
        if first["counts"]:
            if first["counts"] not in cpm_cache:
                cpm_cache[first["counts"]] = _cpm(directory / first["counts"])
            cpm = cpm_cache[first["counts"]]
            if cpm is not None:
                ranked = consensus or first["significant"] or [r[0] for r in first["rows"]]
                order = {r[0]: i for i, r in enumerate(first["rows"])}
                genes = sorted(ranked, key=lambda g: order.get(g, 10 ** 9))[:HEATMAP_ROWS]
                lfc = {r[0]: r[3] or 0 for r in first["rows"]}
                genes.sort(key=lambda g: -lfc.get(g, 0))
                pair = {first["a"], first["b"]}
                heat = _heatmap(cpm, genes, [(n, g) for n, g in samples if g in pair])
        lfc_of = {r[0]: r[3] for r in first["rows"]}
        comparisons.append({"key": "|".join(key), "library": library, "reference": reference,
                            "reference_label": _short(reference), "strand": strand, "count_type": count_type, "comparison": comparison, "a": first["a"], "b": first["b"],
                            "analyses": [i["id"] for i in items], "methods": [i["method"] for i in items],
                            "consensus_up": [g for g in consensus if (lfc_of.get(g) or 0) > 0],
                            "consensus_down": [g for g in consensus if (lfc_of.get(g) or 0) < 0],
                            "heatmap": heat})
    for an in analyses:
        del an["significant"]
    summary = {"analyses": analyses, "comparisons": comparisons, "fdr": fdr, "log2fc": min_lfc,
               "samples": [{"name": n, "group": g} for n, g in samples]}
    (directory / DE_SUMMARY).write_text(json.dumps(summary, separators=(",", ":")))
    log(f"{len(comparisons)} comparison{'s' if len(comparisons) != 1 else ''}, {len(analyses)} DE tables")
    return _de_files(directory, sheets)


def _de_files(directory: Path, sheets: list[tuple[str, pd.DataFrame]]) -> list[dict]:
    import zipfile
    results = []
    names = set()
    with pd.ExcelWriter(directory / "de_results.xlsx") as xl:
        for title, df in sheets:
            name = "".join(ch for ch in title if ch not in "[]:*?/\\")[:31]
            base, i = name, 2
            while name in names:
                name = f"{base[:28]}~{i}"
                i += 1
            names.add(name)
            out = df.rename(columns={"gene": "name", "lfc": "log2FoldChange", "p": "pvalue", "padj": "padj"})
            out.to_excel(xl, sheet_name=name, index=False)
    results.append({"name": "de_results.xlsx", "label": f"All DE tables (Excel, {len(sheets)} sheets)"})
    target = directory / "srnade_results.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in sorted((directory / DE_DIR).rglob("*")):
            if p.is_file() and not p.is_symlink():
                zf.write(p, Path("srnade") / p.relative_to(directory / DE_DIR))
        for extra in ("de_input.csv", "srnade.log"):
            if (directory / extra).is_file():
                zf.write(directory / extra, Path("srnade") / extra)
    results.append({"name": "srnade_results.zip", "label": "Complete sRNAde output (ZIP)"})
    return results


def load_de_summary(job) -> dict | None:
    try:
        return json.loads((job.directory() / DE_SUMMARY).read_text())
    except (OSError, ValueError):
        return None
