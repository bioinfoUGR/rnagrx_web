import gzip
import io
import subprocess
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connections
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from misra import index_store
from misra.accessions import resolve
from misra.engine import Catalog
from misra.indexer import build_index
from misra.models import Job

from .factories import sample_records, sample_studies


class AccessionTests(SimpleTestCase):
    def setUp(self):
        self.cat = Catalog(Catalog.build(sample_records(), sample_studies()))

    def test_resolve_mixed_input(self):
        res = resolve("srx001, SRX004;SRX999\nSRP2 ERP9 hello SRX001", self.cat)
        self.assertEqual(res.exp_ids, ["SRX001", "SRX004", "SRX005"])
        self.assertEqual(res.n_from_experiments, 2)
        self.assertEqual(res.n_from_studies, 1)  # SRX005; SRX004 was listed directly
        self.assertEqual(res.unknown_experiments, ["SRX999"])
        self.assertEqual(res.unknown_studies, ["ERP9"])
        self.assertEqual(res.invalid, ["HELLO"])

    def test_empty(self):
        self.assertEqual(resolve("  ", self.cat).exp_ids, [])


def fake_srnahelper(cmd, **kwargs):
    """Stands in for `sRNAhelper prf`: writes the archive it was asked for."""
    out = cmd[cmd.index("--outfile") + 1]
    fmt = cmd[cmd.index("--outformat") + 1]
    Path(f"{out}.{fmt}").write_bytes(b"archive")
    return subprocess.CompletedProcess(cmd, 0)


class ToolJobTests(TestCase):
    databases = {"default", "misra"}

    @classmethod
    def setUpTestData(cls):
        build_index(sample_records(), sample_studies())
        with connections["misra"].cursor() as c:
            c.execute("CREATE TABLE mgdb (id INTEGER PRIMARY KEY, exp_id TEXT, mirna_name TEXT, "
                      "rc REAL, rpm_lib REAL, rpm_total REAL)")
            rows = [("SRX001", "Hsa-Let-7-P1", 100, 10, 1), ("SRX001", "Hsa-Mir-21", 50, 5, .5),
                    ("SRX002", "Hsa-Mir-21", 7, 1, .1)]
            c.executemany("INSERT INTO mgdb (exp_id, mirna_name, rc, rpm_lib, rpm_total) VALUES (%s, %s, %s, %s, %s)",
                          rows)
            c.execute("CREATE TABLE read_files (id INTEGER PRIMARY KEY, exp_id TEXT, relative_path TEXT, "
                      "file_type TEXT)")
            c.execute("INSERT INTO read_files (exp_id, relative_path, file_type) VALUES "
                      "('SRX001', 'SRP1/SRX001/SRX001.fa.gz', 'reads_collapsed')")
        reads = Path(settings.MISRA_STORAGE) / "SRP1" / "SRX001"
        reads.mkdir(parents=True, exist_ok=True)
        with gzip.open(reads / "SRX001.fa.gz", "wt") as fh:
            fh.write(">1#10\nTGAGGTAGTAGTTTGTGCTGTT\n")

    def setUp(self):
        index_store.reset_for_tests()

    def test_tools_pages(self):
        self.assertContains(self.client.get(reverse("misra:tools")), "Expression matrix")
        self.assertContains(self.client.get(reverse("misra:tool", args=["matrix"])), "miRNA reference")
        self.assertContains(self.client.get(reverse("misra:tool", args=["mirna"])), "coming soon")
        self.assertEqual(self.client.get(reverse("misra:tool", args=["nope"])).status_code, 404)

    def test_check_accessions(self):
        r = self.client.post(reverse("misra:check_accessions"), {"accessions": "SRP1 SRX999", "tool": "matrix"})
        self.assertContains(r, "3 miSRA experiments")
        self.assertContains(r, "SRX999")

    def test_matrix_job(self):
        r = self.client.post(reverse("misra:tool", args=["matrix"]),
                             {"accessions": "SRX001 SRX002 SRX003", "db": "mgdb", "value": "rc"})
        job = Job.objects.get()
        self.assertRedirects(r, job.get_absolute_url())
        self.assertEqual(job.status, Job.DONE, job.message)
        names = [x["name"] for x in job.results]
        self.assertEqual(names, ["matrix_mgdb_rc.tsv.gz", "experiments_without_values.txt",
                                 "experiments_metadata.tsv"])
        r = self.client.get(reverse("misra:job_file", args=[job.slug, "matrix_mgdb_rc.tsv.gz"]))
        text = gzip.decompress(b"".join(r.streaming_content)).decode()
        self.assertEqual(text.splitlines(), ["miRNA\tSRX001\tSRX002", "Hsa-Let-7-P1\t100.0\t0.0",
                                             "Hsa-Mir-21\t50.0\t7.0"])
        page = self.client.get(job.get_absolute_url())
        self.assertContains(page, "finished")
        self.assertContains(page, "2 miRNAs × 2 experiments")
        # only listed result files can be downloaded
        self.assertEqual(self.client.get(reverse("misra:job_file", args=[job.slug, "job.log"])).status_code, 404)

    def test_download_job_from_cart(self):
        self.client.post(reverse("misra:cart_add"), {"mode": "ids", "exp": ["SRX001", "SRX002"]})
        with mock.patch("misra.runners.subprocess.run", side_effect=fake_srnahelper) as run:
            self.client.post(reverse("misra:tool", args=["download"]),
                             {"source": "cart", "sep": "#", "outformat": "zip", "min_rc": 2, "min_len": 18,
                              "max_len": 26})
        job = Job.objects.get()
        self.assertEqual(job.status, Job.DONE, job.message)
        self.assertEqual(job.source, "cart")
        cmd = run.call_args.args[0]
        self.assertIn("prf", cmd)
        self.assertEqual(cmd[cmd.index("--min_read_count") + 1], "2")
        inputs = (job.directory() / "input_files.txt").read_text().split()
        self.assertEqual([Path(p).name for p in inputs], ["SRX001.fa.gz"])
        self.assertEqual([x["name"] for x in job.results],
                         ["misra_reads.zip", "experiments_without_reads.txt", "experiments_metadata.tsv"])
        self.assertEqual((job.directory() / "experiments_without_reads.txt").read_text(), "SRX002\n")

    @override_settings(MISRA_LIMIT_MATRIX=2)
    def test_limit(self):
        r = self.client.post(reverse("misra:tool", args=["matrix"]), {"accessions": "SRP1", "db": "mgdb",
                                                                     "value": "rc"})
        self.assertContains(r, "at most 2 per job")
        self.assertFalse(Job.objects.exists())

    def test_nothing_found(self):
        r = self.client.post(reverse("misra:tool", args=["matrix"]), {"accessions": "SRX999", "db": "mgdb",
                                                                     "value": "rc"})
        self.assertContains(r, "No miSRA experiments found")

    def test_failed_job_reports_error(self):
        self.client.post(reverse("misra:tool", args=["matrix"]), {"accessions": "ERX006", "db": "mgdb", "value": "rc"})
        job = Job.objects.get()
        self.assertEqual(job.status, Job.FAILED)
        self.assertContains(self.client.get(job.get_absolute_url()), "None of the experiments has MirGeneDB")

    def test_cleanup_expires_old_jobs(self):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone
        job = Job.objects.create(tool="matrix", exp_ids="SRX001", n_experiments=1, status=Job.DONE,
                                 results=[{"name": "x", "label": "x", "size": 1}])
        job.directory().mkdir(parents=True)
        (job.directory() / "x").write_text("x")
        Job.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(days=15))
        call_command("misra_cleanup", stdout=io.StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, Job.EXPIRED)
        self.assertFalse(job.directory().exists())
