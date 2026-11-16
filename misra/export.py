"""Experiment metadata as TSV (cart/selection download and job results)."""
from __future__ import annotations

import csv

from .models import ExperimentRow

TSV_FIELDS = [f.name for f in ExperimentRow._meta.concrete_fields if f.name not in ("search_text", "created_at")]


class _Echo:
    def write(self, value):
        return value


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def tsv_lines(ids):
    """Header + one line per experiment (sorted by accession), as strings."""
    writer = csv.writer(_Echo(), delimiter="\t", lineterminator="\n")
    yield writer.writerow(TSV_FIELDS)
    ids_sorted = sorted(ids)
    for i in range(0, len(ids_sorted), 2000):
        chunk = ExperimentRow.objects.filter(exp_id__in=ids_sorted[i:i + 2000]).order_by("exp_id")
        for values in chunk.values_list(*TSV_FIELDS):
            yield writer.writerow([_cell(v) for v in values])


def write_tsv(ids, path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for line in tsv_lines(ids):
            fh.write(line)
