import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from jobs import datasets
from jobs.models import DataFile, Job
from srnatoolbox import species
from srnatoolbox.forms import SrnabenchForm

FASTQ = b"@r1\nTGAGGTAGTAGGTTGTATAGTT\n+\nIIIIIIIIIIIIIIIIIIIIII\n"
DB = Path(tempfile.mkdtemp(prefix="srnatoolbox_db_test_"))
(DB / "mirna").mkdir()
(DB / "species.json").write_text(json.dumps({
    "GCF_000001405.40": {"taxon_id": "9606", "common_name": "human", "organism_name": "Homo sapiens"},
    "GCF_036323735.1": {"taxon_id": "10116", "common_name": "Norway rat", "organism_name": "Rattus norvegicus"}}))
(DB / "mirna" / "mgdb3_Species_TaxonID.txt").write_text("9606\tHomo sapiens\tHsa\n10116\tRattus norvegicus\tRno\n")
(DB / "mirna" / "miRBase_Species_TaxonID.txt").write_text(
    "9606\tHomo sapiens\thsa\n34506\tStrongyloides ratti\tstr\n7668\tStrongylocentrotus purpuratus\tspu\n")
(DB / "mirna" / "pMiren_Species_TaxonID.txt").write_text("3702\tArabidopsis thaliana\tAth\n")
(DB / "mirna" / "miRCarta_Species_TaxonID.txt").write_text("34506\tStrongyloides ratti\tstr\n")


def fake_srnabench(cmd, **kwargs):
    """Stands in for `sRNAbench --samples` (and `sRNAhelper ma`), writing the real output layout: one directory
    per sample under -o, results.json with paths relative to the working directory. Samples named *bad* fail."""
    cmd = [str(c) for c in cmd]
    cwd = Path(kwargs["cwd"])
    if Path(cmd[0]).name == "sRNAhelper":
        Path(cmd[cmd.index("--outfile") + 1]).write_text("preName\tpreSeq\nHsa-Mir-21_pri\tACGT\n")
        return subprocess.CompletedProcess(cmd, 0)
    out = cmd[cmd.index("-o") + 1]
    rows = (cwd / cmd[cmd.index("--samples") + 1]).read_text().splitlines()[1:]
    meta = ["file,name,filepath,results"]
    for i, line in enumerate(rows):
        files, name = line.split("\t")[:2]
        assert all(Path(f).is_file() for f in files.split(":")), files
        d = cwd / out / name
        (d / "stat").mkdir(parents=True)
        (d / "chunks").mkdir()
        (d / "reads.fa").write_text(">1#5\nACGT\n")
        (d / "Hsa_mgdb.bowtie").write_text("alignment")
        if "bad" in name:
            (d / "log.txt").write_text("2026 [INFO] x\n2026 [ERROR] srnatoolbox: No reads left after adapter trimming\n")
            continue
        rel = f"{out}/{name}"
        (d / "Hsa_mgdb.tsv").write_text("name\tUR\tRC\tRCadj\tRPM_lib_adj\tRPM_total_adj\tpreMiRNA string\n"
                                        f"Hsa-Mir-21_5p\t3\t{100 * (i + 1)}\t{100 * (i + 1)}\t600000\t{1000 * (i + 1)}\tHsa-Mir-21_pri,1,22\n"
                                        "Hsa-Let-7a_5p\t2\t50\t50\t400000\t500\tHsa-Let-7a_pri,1,22\n")
        (d / "trna.tsv").write_text("name\tUR\tRC\tRCadj\tRPM_lib_adj\tRPM_total_adj\ntRNA-Gly\t2\t20\t20\t1\t200\n")
        (d / "stat" / "mapping_stat.tsv").write_text("rna_type\tur\tur_perc\trc\trc_perc\nHsa-mgdb_+\t5\t50\t150\t60\n"
                                                     "tRNA_+\t1\t10\t20\t10\nunassigned\t4\t40\t80\t30\n")
        (d / "stat" / "reads_length.tsv").write_text("Read length\tur\tur_perc\trc\trc_perc\trpm\n"
                                                     "21\t1\t10\t20\t20\t0\n22\t2\t20\t80\t80\t0\n")
        (d / "results.json").write_text(json.dumps({
            "parameters": {"protocol": "Illumina"}, "rc": 1000.0, "ur": 50,
            "preproc": {"raw_reads": 1200.0, "adapter_dimer_reads": 100, "ultra_short_reads": 50,
                        "length_filtered_reads": 50, "reads": 1000.0, "unique_reads": 50,
                        "files": {"read_length": {"name": f"{rel}/stat/reads_length.tsv"}}},
            "mirna": {"Hsa_mgdb": {"rcadj_mature": 150.0, "files": {"mature_MA": {"name": f"{rel}/Hsa_mgdb.tsv"}}}},
            "trna": {"tRNA": {"rcadj": 20.0, "files": {"senseMA": {"name": f"{rel}/trna.tsv"}}}},
            "libs": {"ncRNA": {"rcadj": 5.0, "seq_count": 2, "reference": {"short_desc": "small non-coding RNAs"}}},
            "postproc": {"files": {"mapping_stat_total": {"name": f"{rel}/stat/mapping_stat.tsv"}}}}))
        meta.append(f"{files},{name},{files},{rel}/results.json")
    (cwd / out / "meta_results.csv").write_text("\n".join(meta) + "\n")
    return subprocess.CompletedProcess(cmd, 0)


@override_settings(SRNATOOLBOX_DB=str(DB), SRNATOOLBOX_ENV="/opt/fake_env")
class SpeciesTests(TestCase):
    def test_index_and_search(self):
        n_genome, n_other = species.counts()
        self.assertEqual((n_genome, n_other), (2, 3))
        rat = species.get("GCF_036323735.1")
        self.assertEqual((rat.taxon, rat.dbs, rat.has_genome), ("10116", ["mg"], True))
        hits = species.search("rat")
        self.assertEqual(hits[0].key, "GCF_036323735.1")  # genome species first
        self.assertEqual({h.key for h in hits}, {"GCF_036323735.1", "taxon:34506", "taxon:7668"})  # purpuRATus
        self.assertEqual(species.search("34506")[0].dbs, ["mb", "mc"])
        self.assertEqual(species.search("thale")[0:1], [])  # only scientific name in the miRNA list
        self.assertEqual(species.search("arabidopsis")[0].key, "taxon:3702")
        r = self.client.get(reverse("srnatoolbox:species_search"), {"q": "human"})
        self.assertContains(r, "Homo sapiens")
        self.assertContains(r, "genome")


@override_settings(SRNATOOLBOX_DB=str(DB))
class FormTests(TestCase):
    def form(self, **data):
        base = {"species": "GCF_000001405.40", "mirdb": "mg", "protocol": "guess", "random_5p": 0, "random_3p": 0,
                "min_len": 15, "min_rc": 1, "mm": 1, "adapter_mm": 1, "min_adapter_length": 10,
                "low_complexity": "on"}
        return SrnabenchForm({**base, **data}, {k: v for k, v in data.items() if isinstance(v, SimpleUploadedFile)})

    def test_valid_and_params(self):
        f = self.form()
        self.assertTrue(f.is_valid(), f.errors)
        p = f.params()
        self.assertEqual((p["accession"], p["taxon"], p["species_name"], p["mirdb"]),
                         ("GCF_000001405.40", "9606", "Homo sapiens (human)", "mg"))

    def test_errors(self):
        self.assertIn("Unknown species", str(self.form(species="nope").errors))
        self.assertIn("no microRNAs of Homo sapiens", str(self.form(mirdb="pm").errors))
        self.assertIn("Needed for your own", str(self.form(mirdb="own").errors))
        self.assertIn("species without genome", str(self.form(species="taxon:34506", mirdb="own").errors))
        self.assertTrue(self.form(species="taxon:34506", mirdb="mc").is_valid())
        self.assertIn("adapter sequence", str(self.form(protocol="custom", adapter="xyz").errors))
        self.assertTrue(self.form(protocol="custom", adapter="tggaattctcgg").is_valid())
        self.assertIn("smaller than the minimum", str(self.form(min_len=20, max_len=18).errors))


@override_settings(SRNATOOLBOX_DB=str(DB), SRNATOOLBOX_ENV="/opt/fake_env")
class SrnabenchJobTests(TestCase):
    def setUp(self):
        self.ds = datasets.create_dataset("srnatoolbox", "sess")
        for name in ["CTL_1.fastq", "CRC_1_L001.fastq", "CRC_1_L002.fastq", "bad_1.fastq"]:
            p = self.ds.directory() / name
            p.write_bytes(FASTQ)
            datasets.register_file(self.ds, p, name, DataFile.UPLOAD, file_format="fastq")
        datasets.add_column(self.ds, "group")
        for s in self.ds.samples.all():
            s.annotations = {"group": "tumour" if s.name.startswith("CRC") else "control"}
            s.save()
        self.ids = list(self.ds.samples.values_list("pk", flat=True))

    def tearDown(self):
        shutil.rmtree(self.ds.directory(), ignore_errors=True)

    def url(self):
        return reverse("srnatoolbox:tool", args=["srnabench"])

    def post(self, **extra):
        data = {"dataset": self.ds.slug, "s": self.ids, "species": "GCF_000001405.40", "mirdb": "mg",
                "protocol": "guess", "random_5p": 0, "random_3p": 0, "min_len": 15, "min_rc": 1, "mm": 1,
                "adapter_mm": 1, "min_adapter_length": 10, "low_complexity": "on", **extra}
        with mock.patch("jobs.commands.subprocess.run", side_effect=fake_srnabench) as run:
            r = self.client.post(self.url(), data)
        return r, run

    def test_form_page(self):
        r = self.client.get(self.url(), {"dataset": self.ds.slug, "s": self.ids})
        self.assertContains(r, "3 samples</strong>")  # the two lanes of CRC_1 are one sample
        self.assertContains(r, "group: control 2, tumour 1")
        with override_settings(SRNABENCH_MAX_SAMPLES=2):
            r = self.client.get(self.url(), {"dataset": self.ds.slug, "s": self.ids})
        self.assertContains(r, "up to 2")
        r = self.client.get(self.url(), {"dataset": self.ds.slug})
        self.assertContains(r, "No samples are selected")

    def test_job_end_to_end(self):
        r, run = self.post(libs=SimpleUploadedFile("piRNA.fa", b">p1\nACGTACGTACGTACGTACGT\n"))
        job = Job.objects.get()
        self.assertRedirects(r, job.get_absolute_url(), fetch_redirect_response=False)
        # the command line
        cmd = [str(c) for c in run.call_args_list[-1].args[0]]
        self.assertEqual(cmd[cmd.index("--accession") + 1], "GCF_000001405.40")
        self.assertEqual(cmd[cmd.index("--mirdb") + 1], "mg")
        self.assertEqual(cmd[cmd.index("--protocol") + 1], "guess")
        self.assertEqual(cmd[cmd.index("--samples_sep") + 1], "\t")
        self.assertIn("--libs", cmd)
        self.assertNotIn("--graphics", cmd)
        # the sample sheet: lanes joined with ':', annotations as columns
        sheet = (job.directory() / "samples.tsv").read_text().splitlines()
        self.assertEqual(sheet[0], "file\tname\tgroup")
        crc = next(line for line in sheet if "\tCRC_1\t" in line)
        self.assertEqual(crc.split("\t")[0].count(":"), 1)
        # finished, with the failed sample reported
        job.refresh_from_db()
        self.assertEqual(job.status, Job.DONE, job.message)
        self.assertEqual(job.inputs["columns"], ["group"])
        self.assertEqual(len(job.inputs["samples"]), 3)
        names = {x["name"] for x in job.results}
        self.assertTrue({"mirna_RC.tsv", "mirna_RPM_total_adj.tsv", "sample_summary.tsv", "results.xlsx",
                         "samples_failed.tsv"} <= names)
        rc = (job.directory() / "mirna_RC.tsv").read_text().splitlines()
        self.assertEqual(rc[0], "microRNA\tCTL_1\tCRC_1")
        # tidied: input copies, chunks and alignments removed
        sample_dir = job.directory() / "bench" / "CTL_1"
        self.assertFalse((sample_dir / "reads.fa").exists())
        self.assertFalse((sample_dir / "chunks").exists())
        self.assertFalse((sample_dir / "Hsa_mgdb.bowtie").exists())

        page = self.client.get(job.get_absolute_url())
        self.assertContains(page, "Hsa-Mir-21_5p")
        self.assertContains(page, "No reads left after adapter trimming")
        self.assertContains(page, "summary-data")
        self.assertContains(page, "Run again with other settings")
        self.assertContains(page, "from=" + job.slug)
        # sample page, files, zip
        r = self.client.get(reverse("srnatoolbox:job_sample", args=[job.slug, "CTL_1"]))
        self.assertContains(r, "Hsa-Let-7a_5p")
        self.assertContains(r, "small non-coding RNAs")
        self.assertContains(r, "tRNA-Gly")
        self.assertContains(r, "CRC_1 →")
        r = self.client.get(reverse("srnatoolbox:job_sample_file", args=[job.slug, "CTL_1", "stat/mapping_stat.tsv"]))
        self.assertEqual(r.status_code, 200)
        for bad in ["../../samples.tsv", "../CRC_1/results.json", "/etc/passwd"]:
            r = self.client.get(reverse("srnatoolbox:job_sample_file", args=[job.slug, "CTL_1", bad]))
            self.assertEqual(r.status_code, 404, bad)
        r = self.client.get(reverse("srnatoolbox:job_sample_zip", args=[job.slug, "CTL_1"]))
        data = b"".join(r.streaming_content)
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            self.assertIn("CTL_1/results.json", zf.namelist())
        self.assertEqual(self.client.get(reverse("srnatoolbox:job_sample", args=[job.slug, "nope"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("srnatoolbox:job_sample", args=[job.slug, "bad_1"])).status_code, 404)
        # the dataset lists the job; the settings page can copy its settings
        self.assertContains(self.client.get(self.ds.get_absolute_url()), job.slug)
        r = self.client.get(self.url(), {"dataset": self.ds.slug, "s": self.ids, "from": job.slug})
        self.assertContains(r, "Homo sapiens (human)")

    def test_taxon_and_custom_adapter(self):
        r, run = self.post(species="taxon:34506", mirdb="mc", protocol="custom", adapter="TGGAATTC", random_3p=4)
        cmd = [str(c) for c in run.call_args_list[-1].args[0]]
        self.assertEqual(cmd[cmd.index("--taxon_id") + 1], "34506")
        self.assertNotIn("--accession", cmd)
        self.assertEqual(cmd[cmd.index("--adapter_sequence") + 1], "TGGAATTC")
        self.assertEqual(cmd[cmd.index("--random_3p") + 1], "4")
        self.assertNotIn("--protocol", cmd)

    def test_own_mirna_reference(self):
        r, run = self.post(mirdb="own", mature=SimpleUploadedFile("m.fa", b">Hsa-Mir-21_5p\nUAGCUUAUCAGACUGAUGUUGA\n"),
                           hairpin=SimpleUploadedFile("h.fa", b">Hsa-Mir-21_pri\nUGUCGGGUAGCUUAUCAGACUGAUGUUGACUG\n"))
        job = Job.objects.get()
        self.assertTrue((job.directory() / "input" / "mature.fa").is_file())
        tools = [Path(str(c.args[0][0])).name for c in run.call_args_list]
        self.assertEqual(tools, ["sRNAhelper", "sRNAbench"])
        cmd = [str(c) for c in run.call_args_list[-1].args[0]]
        self.assertIn("--mirna", cmd)
        self.assertNotIn("--mirdb", cmd)

    def test_trimmed_reads_and_errors_keep_the_form(self):
        r, run = self.post(protocol="trimmed")
        self.assertNotIn("--protocol", [str(c) for c in run.call_args_list[-1].args[0]])
        Job.objects.all().delete()
        r, run = self.post(species="")
        self.assertContains(r, "Please choose a species")
        self.assertFalse(Job.objects.exists())

    def test_sample_files_gone(self):
        DataFile.objects.get(name="CTL_1.fastq").path().unlink()
        self.post()
        job = Job.objects.get()
        self.assertEqual(job.status, Job.FAILED)
        self.assertIn("Read files of sample CTL_1", job.message)
        self.assertContains(self.client.get(job.get_absolute_url()), "The job failed")
