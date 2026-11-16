"""Reading miRNA/tRNA expression values from misradb2 (shared by the API and the Expression matrix tool).

Table and column names never come from user input: they are looked up in REFERENCES / VALUES.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pandas as pd
from django.db import connections

CHUNK = 500


@dataclass(frozen=True)
class Reference:
    key: str  # public name (API parameter value)
    table: str  # misradb2 table
    feature_col: str
    feature: str  # "miRNA" or "tRNA"
    label: str
    has_isomirs: bool = False
    has_seeds: bool = False
    has_fragments: bool = False


REFERENCES = {r.key: r for r in [
    Reference("mirgenedb", "mgdb", "mirna_name", "miRNA", "MirGeneDB", has_isomirs=True, has_seeds=True),
    Reference("mirbase", "mirbase", "mirna_name", "miRNA", "miRBase", has_isomirs=True, has_seeds=True),
    Reference("pmiren", "pmiren", "mirna_name", "miRNA", "PmiREN (plants)", has_isomirs=True, has_seeds=True),
    Reference("trna-nuclear", "nuc_trna", "trna_name", "tRNA", "nuclear tRNAs", has_fragments=True),
    Reference("trna-mito", "mito_trna", "trna_name", "tRNA", "mitochondrial tRNAs", has_fragments=True),
]}
REFERENCE_BY_TABLE = {r.table: r for r in REFERENCES.values()}

# public value name -> (column, description)
VALUES = {
    "read_count": ("rc", "reads assigned to the feature"),
    "adjusted_read_count": ("rc_adj", "read count with multi-mapping reads split between their features"),
    "rpm_library": ("rpm_lib", "adjusted read count per million reads assigned to the reference (miRNAs or tRNAs)"),
    "rpm_total": ("rpm_total", "adjusted read count per million reads in the analysis"),
}
VALUE_BY_COLUMN = {col: name for name, (col, _) in VALUES.items()}


def _in(n: int) -> str:
    return ", ".join(["%s"] * n)


def load_values(ref: Reference, ids: list[str], columns: tuple[str, ...], progress=None) -> pd.DataFrame:
    """Rows (exp_id, feature, *columns) for the given experiments; columns are table columns from VALUES."""
    allowed = {c for c, _ in VALUES.values()} | {"seed", "iso_mirs", "fragment_class"}
    if not set(columns) <= allowed:
        raise ValueError("unknown column")
    cols = ", ".join(columns)
    frames = []
    with connections["misra"].cursor() as cur:
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            cur.execute(f"SELECT exp_id, {ref.feature_col}, {cols} FROM {ref.table} "
                        f"WHERE exp_id IN ({_in(len(chunk))})", chunk)
            rows = cur.fetchall()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["exp_id", "feature", *columns]))
            if progress:
                progress(min(i + CHUNK, len(ids)), len(ids))
    if not frames:
        return pd.DataFrame(columns=["exp_id", "feature", *columns])
    return pd.concat(frames, ignore_index=True)


def load_feature(ref: Reference, feature: str) -> pd.DataFrame:
    """All rows of one miRNA/tRNA across the database (uses the (name, rpm_total) index)."""
    cols = [c for c, _ in VALUES.values()]
    with connections["misra"].cursor() as cur:
        cur.execute(f"SELECT exp_id, {', '.join(cols)} FROM {ref.table} WHERE {ref.feature_col} = %s", [feature])
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["exp_id", *cols])
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def matrix(data: pd.DataFrame, column: str, ids: list[str], index: str = "feature") -> tuple[pd.DataFrame, list[str]]:
    """Features x experiments (in the order of `ids`, experiments without values left out) and the
    experiments without values."""
    if data.empty:
        return pd.DataFrame(), list(ids)
    data = data.copy()
    data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0)
    m = data.pivot_table(index=index, columns="exp_id", values=column, aggfunc="sum", fill_value=0)
    present = [e for e in ids if e in m.columns]
    return m[present].sort_index(), [e for e in ids if e not in m.columns]


def parse_json_column(value):
    """JSON text from a longtext column; None for NULL/NaN/invalid values."""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, (str, bytes, bytearray)) or not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None
