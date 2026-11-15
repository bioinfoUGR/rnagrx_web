"""Building the miSRA search index from misradb2 (used by `manage.py misra_build_index`)."""
from __future__ import annotations

import logging
import os
from collections import Counter, defaultdict

from django.db import connection, connections, transaction
from django.utils import timezone

from . import index_store
from .catalog import CAT_KEYS, experiment_rows, study_rows
from .engine import Catalog
from .models import ExperimentRow, IndexBuild, StudyRow

log = logging.getLogger(__name__)

STUDY_SIZE_BINS = [(1, 1), (2, 5), (6, 10), (11, 25), (26, 50), (51, 100), (101, None)]
KEEP_ARRAY_FILES = 3


def read_misradb(batch: int = 2000) -> tuple[list[dict], dict[str, dict]]:
    with connections["misra"].cursor() as cur:
        studies = study_rows(cur)
        titles = {k: v["title"] for k, v in studies.items()}
        records = list(experiment_rows(cur, titles, batch=batch))
    return records, studies


def study_records(records: list[dict], studies: dict[str, dict]) -> list[dict]:
    """One row per study that has at least one experiment in the index."""
    n = Counter(r["study"] for r in records)
    organisms = defaultdict(set)
    for r in records:
        if r["organism"]:
            organisms[r["study"]].add(r["organism"])
    out = []
    for sid, count in sorted(n.items()):
        s = studies.get(sid) or {"study_id": sid, "title": "", "abstract": "", "pubmed_ids": "", "created_at": None}
        out.append({**s, "study_id": sid, "n_experiments": count, "organisms": "; ".join(sorted(organisms[sid]))})
    return out


def _fit(value, max_length):
    if max_length and isinstance(value, str) and len(value) > max_length:
        return value[: max_length - 1] + "…"
    return value


def _model_rows(model, records):
    fields = [f for f in model._meta.concrete_fields]
    cols = [f.column for f in fields]
    spec = [(f.attname, getattr(f, "max_length", None)) for f in fields]
    return cols, [tuple(_fit(r.get(n), m) for n, m in spec) for r in records]


def write_tables(records: list[dict], studies: list[dict]) -> None:
    """Replace the SQL copies. On MariaDB the new rows go into *_new tables that are
    swapped in with one atomic RENAME, so searches never see a half-written index."""
    pairs = [(ExperimentRow, records), (StudyRow, studies)]
    if connection.vendor != "mysql":
        with transaction.atomic():
            for model, rows in pairs:
                model.objects.all().delete()
                model.objects.bulk_create([model(**{f.attname: r.get(f.attname) for f in model._meta.concrete_fields})
                                           for r in rows], batch_size=2000)
        return
    qn = connection.ops.quote_name
    with connection.cursor() as c:
        for model, rows in pairs:
            t = model._meta.db_table
            c.execute(f"DROP TABLE IF EXISTS {qn(t + '_new')}")
            c.execute(f"CREATE TABLE {qn(t + '_new')} LIKE {qn(t)}")
            cols, values = _model_rows(model, rows)
            sql = "INSERT INTO {} ({}) VALUES ({})".format(
                qn(t + "_new"), ", ".join(qn(col) for col in cols), ", ".join(["%s"] * len(cols)))
            for i in range(0, len(values), 2000):
                c.executemany(sql, values[i:i + 2000])
        renames = []
        for model, _ in pairs:
            t = model._meta.db_table
            c.execute(f"DROP TABLE IF EXISTS {qn(t + '_old')}")
            renames += [f"{qn(t)} TO {qn(t + '_old')}", f"{qn(t + '_new')} TO {qn(t)}"]
        c.execute("RENAME TABLE " + ", ".join(renames))
        for model, _ in pairs:
            c.execute(f"DROP TABLE IF EXISTS {qn(model._meta.db_table + '_old')}")


def compute_stats(records: list[dict], studies: list[dict]) -> dict:
    def top(key, n=15):
        return Counter(r[key] for r in records if r.get(key)).most_common(n)

    sizes = []
    for lo, hi in STUDY_SIZE_BINS:
        label = str(lo) if lo == hi else (f"{lo}–{hi}" if hi else f"> {lo - 1}")
        count = sum(1 for s in studies if s["n_experiments"] >= lo and (hi is None or s["n_experiments"] <= hi))
        sizes.append([label, count])
    return {
        "n_experiments": len(records),
        "n_studies": len(studies),
        "n_organisms": len({r["organism"] for r in records if r["organism"]}),
        "total_reads": int(sum(r["read_count"] or 0 for r in records)),
        "top": {k: top(k) for k in ("organism", "tax_kingdom", "sample_class", "tissue", "cell_line",
                                    "disease_state", "protocol", "availability")},
        "annotated": {k: sum(1 for r in records if r.get(k)) for k in CAT_KEYS},
        "study_sizes": sizes,
    }


def purge_old_arrays(current_id: int) -> None:
    files = sorted(index_store.array_path(0).parent.glob("catalog_*.npz"),
                   key=lambda p: int(p.stem.split("_")[1]) if p.stem.split("_")[1].isdigit() else -1)
    for p in files[:-KEEP_ARRAY_FILES]:
        if p != index_store.array_path(current_id):
            p.unlink(missing_ok=True)


def build_index(records: list[dict], studies: dict[str, dict]) -> IndexBuild:
    """Write SQL copies + arrays for the given records and mark the build as current."""
    build = IndexBuild.objects.create()
    try:
        srecs = study_records(records, studies)
        write_tables(records, srecs)
        arrays = Catalog.build(records, studies)
        path = index_store.array_path(build.pk)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        Catalog.save(arrays, tmp)
        os.replace(tmp, path)
        build.stats = compute_stats(records, srecs)
        build.n_experiments = len(records)
        build.n_studies = len(srecs)
        build.status = "done"
        build.finished_at = timezone.now()
        build.save()
        index_store.forget_build_id()
        purge_old_arrays(build.pk)
    except Exception as exc:
        build.status = "failed"
        build.message = repr(exc)
        build.finished_at = timezone.now()
        build.save()
        raise
    return build
