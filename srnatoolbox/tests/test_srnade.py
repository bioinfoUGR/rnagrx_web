import csv
import io
import itertools
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from jobs import datasets
from jobs.models import DataFile, Job
from srnatoolbox.de import read_de_table

from .test_srnabench import DB, FASTQ, fake_srnabench

METHODS = ["DESeq2", "edgeR", "limma"]


def fake_srnade(cmd, **kwargs):
    """Stands in for `sRNAde diffexpr`: reads the meta CSV and the per-sample results.json through the job's bench
    link (as the real tool does) and writes the layout of sRNAde 2026 (results.json with absolute paths, one table
    per method and pair of groups). Groups with one sample give fold changes only (p = 1)."""
    if Path(str(cmd[0])).name != "sRNAde":
        return fake_srnabench(cmd, **kwargs)
    cmd = [str(c) for c in cmd]
    cwd = Path(kwargs["cwd"])
    assert cmd[1] == "diffexpr"
    arg = lambda name: cmd[cmd.index(name) + 1]  # noqa: E731
    column = arg("--annotation")
    rows = list(csv.DictReader(open(cwd / arg("-i"))))
    for r in rows:
        json.loads((cwd / r["results"]).read_text())  # must resolve through the bench link
    groups = sorted({r[column] for r in rows})
    size = {g: sum(r[column] == g for r in rows) for g in groups}
    out = cwd / arg("-o")
    base = out / "mirna" / "Hsa_mgdb" / "expression"
    de_dir = base / column / "de_rcadj"
    de_dir.mkdir(parents=True)
    (base / "counts_rcadj.tsv").write_text(
        "Mirna\t" + "\t".join(r["name"] for r in rows) + "\n" +
        "".join(f"Hsa-Mir-{k}_5p\t" + "\t".join(str((k + 1) * (10 + 5 * i) if r[column] == groups[0] else (k + 1) * 3)
                                                for i, r in enumerate(rows)) + "\n" for k in range(5)))
    methods = [arg("--de_method")] if "--de_method" in cmd else METHODS
    de_results = {}
    for m in methods:
        pairwise = {}
        for a, b in itertools.combinations(groups, 2):
            stats = size[a] > 1 and size[b] > 1
            name = f"{b}_vs_{a}"
            path = de_dir / f"{m}_results_{name}_{column}.tsv"
            lines = [f"gene\tmean_{b}\tmean_{a}\tlog2FoldChange\tpvalue\tpadj"]
            for k in range(5):
                p = (1e-6 * (k + 1) if k < 3 else 0.5) if stats else 1.0
                lfc = (-2.5 if k % 2 else 2.0) if k < 3 else 0.1
                if m == "edgeR" and k == 2 and stats:
                    p = 0.2  # edgeR disagrees on one, so the consensus has two
                lines.append(f"Hsa-Mir-{k}_5p\t{10 + k}\t{5 + k}\t{lfc}\t{p}\t{min(1.0, p * 5)}")
            path.write_text("\n".join(lines) + "\n")
            pairwise[name] = str(path)
        de_results[m] = {"method": m, "annotation": column, "groups": groups,
                         "files": {"counts": str(base / "counts_rcadj.tsv"), "pairwise": pairwise, "global": None}}
    (out / "results.json").write_text(json.dumps(
        {"mirna": {"Hsa_mgdb": {"expression": {"rcadj": {column: {"de_results": de_results, "plots": []}}}}}}))
    return subprocess.CompletedProcess(cmd, 0)


@override_settings(SRNATOOLBOX_DB=str(DB), SRNATOOLBOX_ENV="/opt/fake_env")
class SrnadeTests(TestCase):
    def setUp(self):
        self.ds = datasets.create_dataset("srnatoolbox", "sess", "CRC serum")
        for name in ["A1", "A2", "A3", "B1", "B2", "B3", "C1", "bad_1"]:
            p = self.ds.directory() / f"{name}.fastq"
            p.write_bytes(FASTQ)
            datasets.register_file(self.ds, p, f"{name}.fastq", DataFile.UPLOAD, file_format="fastq")
        datasets.add_column(self.ds, "group")
        for s in self.ds.samples.all():
            s.annotations = {"group": {"A": "tumour", "B": "control", "C": "other"}.get(s.name[0], "")}
            s.save()
        data = {"dataset": self.ds.slug, "s": list(self.ds.samples.values_list("pk", flat=True)),
                "species": "GCF_000001405.40", "mirdb": "mg", "protocol": "guess", "random_5p": 0, "random_3p": 0,
                "min_len": 15, "min_rc": 1, "mm": 1, "adapter_mm": 1, "min_adapter_length": 10}
        with mock.patch("jobs.commands.subprocess.run", side_effect=fake_srnabench):
            self.client.post(reverse("srnatoolbox:tool", args=["srnabench"]), data)
        self.bench = Job.objects.get(tool="srnabench")
        self.assertEqual(self.bench.status, Job.DONE, self.bench.message)

    def tearDown(self):
        shutil.rmtree(self.ds.directory(), ignore_errors=True)

    def form_url(self, job=None):
        return reverse("srnatoolbox:srnade", args=[(job or self.bench).slug])

    def post(self, **extra):
        data = {"column": "group", "groups": ["tumour", "control"],
                "samples": ["A1", "A2", "A3", "B1", "B2", "B3", "C1"], "library_type": "mirna", "method": "all",
                "fdr": 0.05, "log2fc": 1, "count_type": "rcadj", "filter_mode": "min_per_group", "min_rc": 1,
                "min_rpm": 1, **extra}
        with mock.patch("jobs.commands.subprocess.run", side_effect=fake_srnade) as run:
            r = self.client.post(self.form_url(), data)
        return r, run

    def test_form_page(self):
        r = self.client.get(self.form_url())
        self.assertContains(r, "sRNAbench job " + self.bench.slug)
        self.assertContains(r, 'value="tumour"')
        self.assertContains(r, "control <span class=\"text-secondary\">(3)</span>")
        self.assertContains(r, 'data-name="A1"')
        self.assertContains(r, "bad_1 failed in sRNAbench")
        self.assertContains(r, "de-page-data")
        self.assertContains(r, "chart-pca")
        # annotating after profiling: a new dataset column is offered
        datasets.add_column(self.ds, "batch")
        s = self.ds.samples.get(name="A1")
        s.annotations = {**s.annotations, "batch": "b1"}
        s.save()
        r = self.client.get(self.form_url(), {"column": "batch"})
        self.assertContains(r, '<option value="batch" selected>')
        self.assertContains(r, "no value in batch")
        # the sRNAbench job page links to the form
        self.assertContains(self.client.get(self.bench.get_absolute_url()), self.form_url())
        # tool page without a job lists the browser's finished sRNAbench jobs
        r = self.client.get(reverse("srnatoolbox:tool", args=["srnade"]))
        self.assertContains(r, self.form_url())

    def test_pca(self):
        r = self.client.get(reverse("srnatoolbox:srnade_pca", args=[self.bench.slug]),
                            {"s": ["A1", "A2", "B1", "B2", "nope"]})
        pca = r.json()["pca"]
        self.assertEqual(len(pca["names"]), 7)  # all profiled samples projected
        self.assertEqual(len(pca["x"]), 7)
        self.assertEqual(len(pca["explained"]), 2)
        r = self.client.get(reverse("srnatoolbox:srnade_pca", args=[self.bench.slug]), {"s": ["A1"]})
        self.assertIsNone(r.json()["pca"])

    def test_unavailable(self):
        self.ds.columns = []
        self.ds.save()
        r = self.client.get(self.form_url())
        self.assertContains(r, "no annotation", status_code=409)
        self.bench.status = Job.RUNNING
        self.bench.save()
        self.assertContains(self.client.get(self.form_url()), "running", status_code=409)

    def test_validation(self):
        r, run = self.post(groups=["tumour"])
        self.assertContains(r, "at least two groups")
        r, run = self.post(samples=["A1", "A2", "C1"], groups=["tumour", "control"])
        self.assertContains(r, "at least two groups")  # control has no ticked sample
        r, run = self.post(library_type="snRNA")
        self.assertContains(r, "Select a valid choice")
        self.assertFalse(Job.objects.filter(tool="srnade").exists())

    def test_job_end_to_end(self):
        r, run = self.post(samples=["A1", "A2", "A3", "B1", "B2", "C1"])  # B3 left out, C1 not in a ticked group
        job = Job.objects.get(tool="srnade")
        self.assertRedirects(r, job.get_absolute_url(), fetch_redirect_response=False)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.DONE, job.message)
        self.assertEqual(job.parent, self.bench)
        self.assertEqual(job.n_items, 5)
        self.assertEqual(sorted(job.inputs["left_out"]), ["B3", "C1"])
        self.assertEqual(job.params["groups"], ["control", "tumour"])
        cmd = [str(c) for c in run.call_args_list[-1].args[0]]
        self.assertEqual(cmd[cmd.index("--annotation") + 1], "group")
        self.assertEqual(cmd[cmd.index("--library_type") + 1], "mirna")
        self.assertNotIn("--de_method", cmd)
        self.assertIn("--graphics_no", cmd)
        # the input: only the kept samples, only the chosen column
        rows = list(csv.DictReader(io.StringIO((job.directory() / "de_input.csv").read_text())))
        self.assertEqual([(r["name"], r["group"]) for r in rows],
                         [("A1", "tumour"), ("A2", "tumour"), ("A3", "tumour"), ("B1", "control"), ("B2", "control")])
        self.assertFalse((job.directory() / "bench").exists())  # the link to the parent is gone
        # summary: one comparison, three methods, consensus of the methods
        de = json.loads((job.directory() / "de_summary.json").read_text())
        self.assertEqual(len(de["comparisons"]), 1)
        c = de["comparisons"][0]
        self.assertEqual((c["a"], c["b"], c["methods"]), ("tumour", "control", METHODS))
        self.assertEqual((c["consensus_up"], c["consensus_down"]), (["Hsa-Mir-0_5p"], ["Hsa-Mir-1_5p"]))
        self.assertEqual(len(c["heatmap"]["samples"]), 5)
        deseq = next(a for a in de["analyses"] if a["method"] == "DESeq2")
        self.assertEqual((deseq["up"], deseq["down"], deseq["tested"], deseq["stats"]), (2, 1, 5, True))
        self.assertEqual(deseq["volcano"]["s"][:3], [1, -1, 1])
        self.assertEqual(deseq["mean_cols"], ["tumour", "control"])  # mean_<a> first, as in sRNAde
        # pages and files
        page = self.client.get(job.get_absolute_url())
        self.assertContains(page, "de-data")
        self.assertContains(page, "left out: B3, C1")
        self.assertContains(page, "de_results.xlsx")
        self.assertContains(page, reverse("srnatoolbox:srnade", args=[self.bench.slug]) + f"?from={job.slug}")
        for name in ("de_results.xlsx", "srnade_results.zip"):
            r = self.client.get(reverse("srnatoolbox:job_file", args=[job.slug, name]))
            self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(job.directory() / "srnade_results.zip")
        self.assertIn("srnade/de_input.csv", z.namelist())
        self.assertFalse(any("bench" in n for n in z.namelist()))
        rel = deseq["file"][len("de/"):]
        r = self.client.get(reverse("srnatoolbox:de_file", args=[job.slug, rel]))
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse("srnatoolbox:de_file", args=[job.slug, "../de_input.csv"]))
        self.assertEqual(r.status_code, 404)
        # listed on the sRNAbench job page; the form can start from its settings
        self.assertContains(self.client.get(self.bench.get_absolute_url()), job.get_absolute_url())
        r = self.client.get(self.form_url(), {"from": job.slug})
        self.assertContains(r, f'<option value="{job.slug}" selected>')
        self.assertNotContains(r, 'value="B3" aria-label="Use B3" checked')

    def test_single_sample_group_and_one_method(self):
        self.post(samples=["A1", "A2", "C1"], groups=["tumour", "other"], method="edgeR")
        job = Job.objects.get(tool="srnade")
        self.assertEqual(job.status, Job.DONE, job.message)
        de = json.loads((job.directory() / "de_summary.json").read_text())
        (a,) = de["analyses"]
        self.assertEqual((a["method"], a["stats"], a["up"], a["down"]), ("edgeR", False, 0, 0))
        self.assertEqual(de["comparisons"][0]["consensus_up"], [])

    def test_parent_results_deleted(self):
        shutil.rmtree(self.bench.directory() / "bench")
        self.post()
        job = Job.objects.get(tool="srnade")
        self.assertEqual(job.status, Job.FAILED)
        self.assertIn("were deleted", job.message)


class ReadTableTests(TestCase):
    def test_edger_style_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.tsv"
            p.write_text("name\tlogFC\tPValue\tFDR\nx\t1.5\t0.001\t0.01\ny\tNA\t0.5\t0.6\n")
            df = read_de_table(p)
        self.assertEqual(list(df["gene"]), ["x", "y"])
        self.assertEqual(float(df.iloc[0]["padj"]), 0.01)
