"""In-memory column index for filtering, counting and paging miSRA experiments.

Every categorical field is stored as an int32 code per experiment (0 = not
annotated) plus a vocabulary of strings; numbers as float64 (NaN = missing).
Experiments are ordered by accession. With 300k experiments and ~25 fields this
takes about 40 MB per process. A filter is a boolean mask, and a facet count is
one np.bincount, so a full page refresh (results + all facet counts) takes
milliseconds instead of one SQL GROUP BY per facet.

The arrays are written by `manage.py misra_build_index` (Catalog.build + save)
and loaded by each web worker (misra.index_store).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .catalog import CAT_KEYS, NUM_KEYS

MISSING = ""  # vocabulary entry 0; in URLs a filter value "" means "not annotated"


@dataclass
class Query:
    filters: dict[str, list[str]] = field(default_factory=dict)  # facet key -> values (OR)
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    text_mask: np.ndarray | None = None  # from the full-text search (misra.textsearch)
    scope_mask: np.ndarray | None = None  # cart or saved selection


@dataclass
class Evaluation:
    base: np.ndarray  # scope, text and ranges
    facet_masks: dict[str, np.ndarray]  # one mask per filtered facet
    mask: np.ndarray  # final selection

    @property
    def count(self) -> int:
        return int(self.mask.sum())


SORTS = {
    # name: (kind, key)
    "id": ("id", None),
    "study": ("cat", "study"),
    "organism": ("cat", "organism"),
    "reads": ("num", "read_count"),
    "mirna": ("num", "mirna_pct"),
    "genome": ("num", "genome_pct"),
}


class Catalog:
    def __init__(self, arrays: dict[str, np.ndarray], build_id: int = 0):
        self.build_id = build_id
        self.exp_id = arrays["exp_id"]
        self.n = len(self.exp_id)
        self.codes = {k: arrays[f"c__{k}"] for k in CAT_KEYS}
        self.vocab = {k: arrays[f"v__{k}"] for k in CAT_KEYS}
        self.nums = {k: arrays[f"n__{k}"] for k in NUM_KEYS}
        self.study_title = arrays["study_title"]  # aligned with vocab["study"]
        self.study_organisms = arrays["study_organisms"]
        self.vocab_index = {k: {v: i for i, v in enumerate(self.vocab[k])} for k in CAT_KEYS}
        self.study_total = np.bincount(self.codes["study"], minlength=len(self.vocab["study"]))
        self._perm_cache: dict[tuple[str, bool], np.ndarray] = {}

    # ---------- building and storage ----------

    @staticmethod
    def build(records: list[dict], studies: dict[str, dict]) -> dict[str, np.ndarray]:
        """Arrays from flat experiment dicts (misra.catalog.experiment_from_db)."""
        records = sorted(records, key=lambda r: r["exp_id"])
        arrays: dict[str, np.ndarray] = {"exp_id": np.array([r["exp_id"] for r in records], dtype=str)}
        if not records:
            arrays["exp_id"] = np.array([], dtype="U1")
        for key in CAT_KEYS:
            values = [r.get(key) or MISSING for r in records]
            vocab = [MISSING] + sorted({v for v in values if v != MISSING}, key=lambda s: (s.casefold(), s))
            index = {v: i for i, v in enumerate(vocab)}
            arrays[f"c__{key}"] = np.array([index[v] for v in values], dtype=np.int32)
            arrays[f"v__{key}"] = np.array(vocab, dtype=str)
        for key in NUM_KEYS:
            arrays[f"n__{key}"] = np.array(
                [np.nan if r.get(key) is None else float(r[key]) for r in records], dtype=np.float64)
        study_vocab = arrays["v__study"]
        organisms: dict[str, set] = {}
        for r in records:
            if r.get("organism"):
                organisms.setdefault(r["study"], set()).add(r["organism"])
        arrays["study_title"] = np.array([(studies.get(s) or {}).get("title", "") for s in study_vocab], dtype=str)
        arrays["study_organisms"] = np.array(["; ".join(sorted(organisms.get(s, ()))) for s in study_vocab], dtype=str)
        return arrays

    @staticmethod
    def save(arrays: dict[str, np.ndarray], path) -> None:
        with open(path, "wb") as fh:
            np.savez(fh, **arrays)

    @classmethod
    def load(cls, path, build_id: int = 0) -> "Catalog":
        with np.load(path, allow_pickle=False) as data:
            arrays = {k: data[k] for k in data.files}
        return cls(arrays, build_id)

    # ---------- accession <-> position ----------

    def positions_of(self, exp_ids) -> np.ndarray:
        """Row positions of the given accessions; unknown accessions are dropped."""
        ids = np.asarray(list(exp_ids), dtype=str)
        if ids.size == 0 or self.n == 0:
            return np.array([], dtype=np.int64)
        idx = np.searchsorted(self.exp_id, ids)
        idx = np.clip(idx, 0, self.n - 1)
        return np.unique(idx[self.exp_id[idx] == ids])

    def mask_of_positions(self, positions) -> np.ndarray:
        mask = np.zeros(self.n, dtype=bool)
        mask[np.asarray(positions, dtype=np.int64)] = True
        return mask

    def mask_of_studies(self, study_ids) -> np.ndarray:
        lut = np.zeros(len(self.vocab["study"]), dtype=bool)
        for s in study_ids:
            code = self.vocab_index["study"].get(s)
            if code is not None:
                lut[code] = True
        return lut[self.codes["study"]]

    # ---------- filtering ----------

    def facet_mask(self, key: str, values) -> np.ndarray:
        lut = np.zeros(len(self.vocab[key]), dtype=bool)
        for v in values:
            code = self.vocab_index[key].get(v)
            if code is not None:
                lut[code] = True
        return lut[self.codes[key]]

    def evaluate(self, q: Query) -> Evaluation:
        base = np.ones(self.n, dtype=bool)
        if q.scope_mask is not None:
            base &= q.scope_mask
        if q.text_mask is not None:
            base &= q.text_mask
        for key, (lo, hi) in q.ranges.items():
            col = self.nums[key]
            m = ~np.isnan(col)
            if lo is not None:
                m &= col >= lo
            if hi is not None:
                m &= col <= hi
            base &= m
        facet_masks = {k: self.facet_mask(k, vals) for k, vals in q.filters.items() if vals and k in self.codes}
        mask = base.copy()
        for m in facet_masks.values():
            mask &= m
        return Evaluation(base=base, facet_masks=facet_masks, mask=mask)

    def facet_counts(self, ev: Evaluation, key: str) -> np.ndarray:
        """Counts per vocabulary code for one facet. Disjunctive: the facet's own filter is
        left out, so the other values of a facet with a ticked value stay selectable."""
        if key in ev.facet_masks:
            m = ev.base.copy()
            for k, fm in ev.facet_masks.items():
                if k != key:
                    m &= fm
        else:
            m = ev.mask
        return np.bincount(self.codes[key][m], minlength=len(self.vocab[key]))

    def facet_values(self, ev: Evaluation, key: str, selected=(), limit: int | None = None,
                     term: str = "", hidden=()) -> tuple[list[dict], int]:
        """Values of one facet with counts, most frequent first; selected values always
        included. Returns (values, number of values with count > 0)."""
        counts = self.facet_counts(ev, key)
        vocab = self.vocab[key]
        selected = set(selected)
        hidden = set(hidden)
        codes = np.nonzero(counts)[0]
        if term:
            t = term.casefold()
            codes = np.array([c for c in codes if t in str(vocab[c]).casefold()], dtype=np.int64)
        codes = np.array([c for c in codes if str(vocab[c]) not in hidden or str(vocab[c]) in selected],
                         dtype=np.int64)
        n_nonzero = len(codes)
        if len(codes):
            order = np.lexsort((codes, -counts[codes]))
            codes = codes[order]
        if limit is not None:
            codes = codes[:limit]
        out = [{"value": str(vocab[c]), "count": int(counts[c]), "selected": str(vocab[c]) in selected}
               for c in codes]
        shown = {o["value"] for o in out}
        for v in selected:
            if v not in shown:
                code = self.vocab_index[key].get(v)
                out.append({"value": v, "count": int(counts[code]) if code is not None else 0, "selected": True})
        return out, n_nonzero

    # ---------- paging ----------

    def _perm(self, sort: str, desc: bool) -> np.ndarray:
        cache_key = (sort, desc)
        if cache_key not in self._perm_cache:
            kind, key = SORTS.get(sort, SORTS["id"])
            ids = np.arange(self.n)
            if kind == "id":
                perm = ids[::-1].copy() if desc else ids
            elif kind == "cat":
                codes = self.codes[key]
                # missing (code 0) always last
                sort_codes = np.where(codes == 0, np.iinfo(np.int32).max, -codes if desc else codes)
                perm = np.lexsort((ids, sort_codes))
            else:
                col = self.nums[key]
                vals = np.where(np.isnan(col), np.inf, -col if desc else col)
                perm = np.lexsort((ids, vals))
            self._perm_cache[cache_key] = perm
        return self._perm_cache[cache_key]

    def page(self, mask: np.ndarray, sort: str = "id", desc: bool = False,
             offset: int = 0, limit: int = 50) -> np.ndarray:
        perm = self._perm(sort, desc)
        selected = perm[mask[perm]]
        return selected[offset:offset + limit]

    def rows(self, positions) -> list[dict]:
        out = []
        for p in positions:
            row = {"exp_id": str(self.exp_id[p]), "pos": int(p)}
            for k in CAT_KEYS:
                row[k] = str(self.vocab[k][self.codes[k][p]])
            for k in NUM_KEYS:
                x = self.nums[k][p]
                row[k] = None if np.isnan(x) else float(x)
            out.append(row)
        return out

    def study_page(self, mask: np.ndarray, sort: str = "matching", desc: bool = True,
                   offset: int = 0, limit: int = 50) -> tuple[list[dict], int]:
        counts = np.bincount(self.codes["study"][mask], minlength=len(self.vocab["study"]))
        codes = np.nonzero(counts)[0]
        codes = codes[codes != 0]
        total = len(codes)
        if sort == "id":
            order = np.argsort(codes)
            if desc:
                order = order[::-1]
        else:  # by number of matching experiments, then accession
            order = np.lexsort((codes, -counts[codes] if desc else counts[codes]))
        codes = codes[order][offset:offset + limit]
        rows = [{
            "study_id": str(self.vocab["study"][c]),
            "title": str(self.study_title[c]),
            "organisms": str(self.study_organisms[c]),
            "matching": int(counts[c]),
            "total": int(self.study_total[c]),
        } for c in codes]
        return rows, total

    def exp_ids(self, mask: np.ndarray) -> list[str]:
        return [str(x) for x in self.exp_id[mask]]
