import time

from django.core.management.base import BaseCommand

from misra.indexer import build_index, read_misradb


class Command(BaseCommand):
    help = "Rebuild the miSRA search index (SQL copy + in-memory arrays) from misradb2."

    def add_arguments(self, parser):
        parser.add_argument("--batch", type=int, default=2000, help="experiments read per query")

    def handle(self, *args, **opts):
        t0 = time.monotonic()
        records, studies = read_misradb(batch=opts["batch"])
        t1 = time.monotonic()
        self.stdout.write(f"read {len(records)} experiments, {len(studies)} studies in {t1 - t0:.1f} s")
        build = build_index(records, studies)
        self.stdout.write(self.style.SUCCESS(
            f"build {build.pk}: {build.n_experiments} experiments, {build.n_studies} studies "
            f"in {time.monotonic() - t0:.1f} s"))
