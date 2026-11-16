import gzip
import io
import json
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


class ToolFixture(TestCase):
    """Index, misradb2 tables (mgdb, read_files) and one read file; no tests of its own."""

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


class ToolJobTests(ToolFixture):
    def test_tools_pages(self):
        self.assertContains(self.client.get(reverse("misra:tools")), "Expression matrix")
        self.assertContains(self.client.get(reverse("misra:tool", args=["matrix"])), "miRNA reference")
        self.assertContains(self.client.get(reverse("misra:tool", args=["libs"])), "Reference sequences (FASTA)")
        self.assertContains(self.client.get(reverse("misra:tool", args=["mirna"])), "Mature miRNA sequences")
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


def fake_srnabench(cmd, **kwargs):
    """Stands in for `sRNAhelper ma` and `sRNAbench --samples`, writing output in the real layout."""
    cmd = [str(c) for c in cmd]
    if Path(cmd[0]).name == "sRNAhelper":
        Path(cmd[cmd.index("--outfile") + 1]).write_text("preName\tpreSeq\nHsa-Mir-21_pri\tACGT\n")
        return subprocess.CompletedProcess(cmd, 0)
    out = Path(cmd[cmd.index("-o") + 1])
    out.mkdir(parents=True, exist_ok=True)
    samples = Path(cmd[cmd.index("--samples") + 1]).read_text().splitlines()[1:]
    meta = ["file,name,filepath,results"]
    for i, line in enumerate(samples):
        f, name = line.split("\t")
        d = out / name
        d.mkdir()
        (d / "reads.fa").write_text(">1#5\nACGT\n")
        if "--libs" in cmd:
            (d / "libs_results").mkdir()
            table = d / "libs_results" / "hairpin_expression_sense.tsv"
            res = {"libs": {"hairpin": {"files": {"senseMA": {"name": str(table)}}}}}
            table.write_text("name\tUR\tRC\tRCadj\tRPM_lib_adj\tRPM_total_adj\n"
                             f"Hsa-Mir-21_pri\t1\t{10 * (i + 1)}\t{10 * (i + 1)}\t1000\t100\n")
        elif "--spike_file" in cmd:
            table = d / "spikes_count.tsv"
            res = {"spike_ins": {"spike_ins": {"file": str(table)}}}
        else:
            table = d / "all_mirna_annot.tsv"
            res = {"mirna": {"all_mirna_annot": {"files": {"mature_MA": {"name": str(table)}}}}}
        if "--libs" not in cmd:
            table.write_text("name\tUR\tRC\tRCadj\tRPM_lib_adj\tRPM_total_adj\n"
                             f"Hsa-Mir-21_5p\t1\t{10 * (i + 1)}\t{10 * (i + 1)}\t1000\t100\n")
        (d / "results.json").write_text(json.dumps({**res, "rc": 1000.0, "ur": 50,
                                                    "preproc": {"raw_reads": 1200.0}}))
        meta.append(f"{f},{name},{f},{d / 'results.json'}")
    (out / "meta_results.csv").write_text("\n".join(meta) + "\n")
    return subprocess.CompletedProcess(cmd, 0)


MATURE = b">Hsa-Mir-21_5p\nUAGCUUAUCAGACUGAUGUUGA\n"
HAIRPIN = b">Hsa-Mir-21_pri\nUGUCGGGUAGCUUAUCAGACUGAUGUUGACUGUUGAAUCUCAUGGCAACACCAGUCGAUGGGCUGUC\n"


class FastaTests(SimpleTestCase):
    def parse(self, content: bytes):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from misra.forms import parse_fasta
        return parse_fasta(SimpleUploadedFile("x.fa", content), "Test")

    def test_valid_with_u_to_t_and_multiline(self):
        fa = self.parse(b">a desc\nacgu\nNNAC\n\n>b\nTTTT\n")
        self.assertEqual(fa.records, [("a", "ACGTNNAC"), ("b", "TTTT")])
        self.assertEqual(fa.text(), ">a\nACGTNNAC\n>b\nTTTT\n")

    def test_errors(self):
        from django import forms
        for content, msg in [(b"ACGT\n", "must start"), (b">a\nAC-GT\n", "invalid character"),
                             (b">a\nAC\n>a\nGT\n", "duplicate"), (b">a\n>b\nAC\n", "is empty"), (b"", "no sequences"),
                             (b"\xff\xfe>a", "plain-text")]:
            with self.assertRaisesMessage(forms.ValidationError, msg):
                self.parse(content)


class SrnabenchJobTests(ToolFixture):
    def submit(self, tool, data, files):
        from django.core.files.uploadedfile import SimpleUploadedFile
        payload = dict(data)
        for key, content in files.items():
            payload[key] = SimpleUploadedFile(f"{key}.fa", content)
        with mock.patch("misra.runners.subprocess.run", side_effect=fake_srnabench) as run:
            response = self.client.post(reverse("misra:tool", args=[tool]), payload)
        return response, run

    def test_mirna_job(self):
        r, run = self.submit("mirna", {"accessions": "SRX001", "mm": 1, "min_rc": 2, "min_len": 18},
                             {"mature": MATURE, "hairpin": HAIRPIN})
        job = Job.objects.get()
        self.assertEqual(job.status, Job.DONE, job.message)
        self.assertEqual(job.params["mature"], "mature.fa (1 sequences, 22 nt)")
        self.assertEqual((job.directory() / "input" / "mature.fa").read_text(),
                         ">Hsa-Mir-21_5p\nTAGCTTATCAGACTGATGTTGA\n")
        bench = [str(c) for c in run.call_args_list[-1].args[0]]
        for opt, val in (("--mm", "1"), ("--min_read_count", "2"), ("--min_read_length", "18")):
            self.assertEqual(bench[bench.index(opt) + 1], val)
        self.assertIn("--notrna", bench)
        names = [x["name"] for x in job.results]
        self.assertEqual(names[:4], ["matrix_RC.tsv", "matrix_RCadj.tsv", "matrix_RPM_lib.tsv", "matrix_RPM_total.tsv"])
        self.assertIn("results.xlsx", names)
        self.assertIn("srnabench_output.zip", names)
        self.assertEqual((job.directory() / "matrix_RC.tsv").read_text(), "miRNA\tSRX001\nHsa-Mir-21_5p\t10\n")
        summary = (job.directory() / "experiment_summary.tsv").read_text().splitlines()
        self.assertEqual(summary[1].split("\t")[-2:], ["10.0", "1.0"])
        import zipfile
        with zipfile.ZipFile(job.directory() / "srnabench_output.zip") as zf:
            self.assertIn("SRX001/results.json", zf.namelist())
            self.assertNotIn("SRX001/reads.fa", zf.namelist())
        self.assertFalse((job.directory() / "bench").exists())

    def test_exact_job_and_missing_reads(self):
        r, run = self.submit("exact", {"accessions": "SRX001 SRX002"}, {"spikes": MATURE})
        job = Job.objects.get()
        self.assertEqual(job.status, Job.DONE, job.message)
        self.assertIn("--spike_file", [str(c) for c in run.call_args.args[0]])
        self.assertEqual((job.directory() / "experiments_without_reads.txt").read_text(), "SRX002\n")
        self.assertEqual((job.directory() / "matrix_RCadj.tsv").read_text(), "sequence\tSRX001\nHsa-Mir-21_5p\t10\n")

    def test_bad_fasta_is_rejected(self):
        r, run = self.submit("exact", {"accessions": "SRX001"}, {"spikes": b"not a fasta"})
        self.assertContains(r, "must start with")
        self.assertFalse(Job.objects.exists())

    def test_libs_job(self):
        r, run = self.submit("libs", {"accessions": "SRX001", "mm": 1}, {"libs": HAIRPIN})
        job = Job.objects.get()
        self.assertEqual(job.status, Job.DONE, job.message)
        cmd = [str(c) for c in run.call_args.args[0]]
        self.assertIn("--libs", cmd)
        self.assertIn("--nomirna", cmd)
        self.assertEqual((job.directory() / "matrix_RC.tsv").read_text(), "reference\tSRX001\nHsa-Mir-21_pri\t10\n")
