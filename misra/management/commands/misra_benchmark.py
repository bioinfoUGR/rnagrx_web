"""Time the browse operations on a synthetic index of N experiments.

The real experiments are copied with new accessions (and new study accessions)
until N is reached. Nothing is written to the database or to the live index.

    python manage.py misra_benchmark --size 300000
"""
import statistics
import time

import numpy as np
from django.core.management.base import BaseCommand

from misra.catalog import CAT_KEYS
from misra.engine import Catalog, Query
from misra.indexer import read_misradb


def _timed(fn, repeat):
    times = []
    for _ in range(repeat):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    return statistics.median(times), times[int(len(times) * 0.95) - 1]


class Command(BaseCommand):
    help = "Benchmark facet counting and paging on a synthetic large index."

    def add_arguments(self, parser):
        parser.add_argument("--size", type=int, default=300_000)
        parser.add_argument("--repeat", type=int, default=30)

    def handle(self, *args, **opts):
        records, studies = read_misradb()
        size = opts["size"]
        copies = []
        i = 0
        while len(copies) < size:
            for r in records:
                if len(copies) >= size:
                    break
                c = dict(r)
                if i:
                    c["exp_id"] = f"{r['exp_id']}_{i}"
                    c["study"] = f"{r['study']}_{i}"
                copies.append(c)
            i += 1
        study_info = {}
        for k in range(i):
            for sid, s in studies.items():
                study_info[sid if k == 0 else f"{sid}_{k}"] = s
        t = time.perf_counter()
        cat = Catalog(Catalog.build(copies, study_info))
        self.stdout.write(f"{cat.n} experiments, {len(cat.vocab['study']) - 1} studies; "
                          f"arrays built in {time.perf_counter() - t:.1f} s")
        size_mb = (sum(a.nbytes for a in cat.codes.values()) + sum(a.nbytes for a in cat.nums.values())
                   + cat.exp_id.nbytes + sum(v.nbytes for v in cat.vocab.values())
                   + cat.study_title.nbytes + cat.study_organisms.nbytes) / 1e6
        self.stdout.write(f"memory of the arrays: {size_mb:.0f} MB")

        facets = [k for k in CAT_KEYS if not k.startswith("tax_") and k != "study"] + ["tax_kingdom"]
        top_org = str(cat.vocab["organism"][np.bincount(cat.codes["organism"])[1:].argmax() + 1])
        top_tissue = str(cat.vocab["tissue"][np.bincount(cat.codes["tissue"])[1:].argmax() + 1])
        scenarios = {
            "no filter": Query(),
            f"organism={top_org}": Query(filters={"organism": [top_org]}),
            f"organism + tissue={top_tissue} + reads>=1e6": Query(
                filters={"organism": [top_org], "tissue": [top_tissue]}, ranges={"read_count": (1e6, None)}),
            "3 facets, 2 values each": Query(filters={
                "sample_class": [str(v) for v in cat.vocab["sample_class"][1:3]],
                "protocol": [str(v) for v in cat.vocab["protocol"][1:3]],
                "availability": [str(v) for v in cat.vocab["availability"][1:3]]}),
        }
        self.stdout.write(f"{'scenario':48} {'matches':>8}  {'facets p50/p95':>16}  {'page p50/p95':>14}  "
                          f"{'studies p50/p95':>16}  (ms, {len(facets)} facets)")
        for name, q in scenarios.items():
            ev = cat.evaluate(q)

            def facets_fn():
                e = cat.evaluate(q)
                for k in facets:
                    cat.facet_values(e, k, selected=q.filters.get(k, ()), limit=8)

            f50, f95 = _timed(facets_fn, opts["repeat"])
            p50, p95 = _timed(lambda: cat.rows(cat.page(ev.mask, "reads", True, 1000, 50)), opts["repeat"])
            s50, s95 = _timed(lambda: cat.study_page(ev.mask), opts["repeat"])
            self.stdout.write(f"{name[:48]:48} {ev.count:>8}  {f50:7.1f}/{f95:7.1f}  {p50:6.1f}/{p95:6.1f}  "
                              f"{s50:7.1f}/{s95:7.1f}")
