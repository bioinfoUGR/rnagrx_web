import json
import shutil

from django.test import TestCase
from django.urls import reverse

from jobs import datasets
from jobs.models import DataFile, Dataset, Sample

FASTQ = b"@r1\nTGAGGTAGTAGGTTGTATAGTT\n+\nIIIIIIIIIIIIIIIIIIIIII\n"


class DatasetPageTests(TestCase):
    def setUp(self):
        r = self.client.post(reverse("srnatoolbox:data_new"), {"title": "Serum CRC"})
        self.ds = Dataset.objects.get()
        self.assertRedirects(r, self.ds.get_absolute_url())

    def tearDown(self):
        shutil.rmtree(self.ds.directory(), ignore_errors=True)

    def add(self, name):
        p = self.ds.directory() / name
        p.write_bytes(FASTQ)
        return datasets.register_file(self.ds, p, name, DataFile.UPLOAD, file_format="fastq")

    def url(self, name, *args):
        return reverse(f"srnatoolbox:{name}", args=[self.ds.slug, *args])

    def test_pages(self):
        r = self.client.get(reverse("srnatoolbox:data"))
        self.assertContains(r, "Serum CRC")
        r = self.client.get(self.ds.get_absolute_url())
        self.assertContains(r, "No samples yet")
        self.assertContains(r, "upload.js")
        self.add("CRC_001_S1_R1_001.fastq")
        r = self.client.get(self.ds.get_absolute_url())
        self.assertContains(r, 'value="CRC_001"')
        self.assertContains(r, "CRC_001_S1_R1_001.fastq")
        # another browser opens the link: it can see and use the dataset
        other = self.client_class()
        self.assertEqual(other.get(self.ds.get_absolute_url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("srnatoolbox:dataset", args=["nope"])).status_code, 404)

    def test_transfers_fragment_signals_new_samples(self):
        r = self.client.get(self.url("dataset_transfers") + "?ready=0")
        self.assertNotIn("HX-Trigger", r)
        self.assertNotContains(r, "every 2s")
        self.add("a.fastq")
        r = self.client.get(self.url("dataset_transfers") + "?ready=0")
        self.assertEqual(r["HX-Trigger"], "samples-changed")
        DataFile.objects.create(dataset=self.ds, name="big.tar", source="link", status="transferring")
        self.assertContains(self.client.get(self.url("dataset_transfers")), "every 2s")

    def test_links(self):
        r = self.client.post(self.url("dataset_links"), {"links": "ftp:/bad\n"})
        self.assertContains(r, "not an http")
        self.assertFalse(DataFile.objects.exists())

    def test_cells_and_columns(self):
        a, b = self.add("a.fastq"), self.add("b.fastq")
        r = self.client.post(self.url("dataset_columns"), {"name": "group"})
        self.assertContains(r, "group")
        sa = Sample.objects.get(name="a")
        r = self.client.post(self.url("dataset_cells"), json.dumps({"edits": [
            {"sample": sa.pk, "field": "column", "column": "group", "value": "tumour"},
            {"sample": sa.pk, "field": "name", "value": "b"}]}), content_type="application/json")
        self.assertEqual(r.json()["saved"], 1)
        self.assertIn(f"{sa.pk}:name", r.json()["errors"])
        sa.refresh_from_db()
        self.assertEqual(sa.annotations, {"group": "tumour"})
        r = self.client.post(self.url("dataset_cells"), "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post(self.url("dataset_columns"), {"name": "group"})
        self.assertContains(r, "already a column")
        r = self.client.post(self.url("dataset_columns"), {"remove": "group"})
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.columns, [])

    def test_sheet_round_trip(self):
        self.add("a.fastq")
        self.add("b.fastq")
        for fmt in ("tsv", "csv", "xlsx"):
            r = self.client.get(self.url("dataset_sheet", fmt))
            self.assertEqual(r.status_code, 200)
            self.assertIn(f"samples_{self.ds.slug}.{fmt}", r["Content-Disposition"])
        self.assertEqual(self.client.get(self.url("dataset_sheet", "exe")).status_code, 404)
        from django.core.files.uploadedfile import SimpleUploadedFile
        sheet = SimpleUploadedFile("sheet.tsv", b"file\tname\tgroup\na.fastq\tctrl\tcontrol\nb.fastq\ttum\ttumour\n")
        r = self.client.post(self.url("dataset_sheet_upload"), {"sheet": sheet})
        self.assertContains(r, "Apply: replace the samples")
        self.assertEqual(sorted(Sample.objects.values_list("name", flat=True)), ["a", "b"])  # not yet applied
        r = self.client.post(self.url("dataset_sheet_apply"))
        self.assertContains(r, "applied: 2 samples")
        self.assertEqual(sorted(Sample.objects.values_list("name", flat=True)), ["ctrl", "tum"])
        bad = SimpleUploadedFile("sheet.csv", b"file,name\nzzz.fastq,x\n")
        r = self.client.post(self.url("dataset_sheet_upload"), {"sheet": bad})
        self.assertContains(r, "not a file of this dataset")
        self.assertContains(r, "disabled")
        r = self.client.post(self.url("dataset_sheet_apply"), {"cancel": "1"})
        self.assertNotContains(r, "Apply: replace")

    def test_remove_rename_delete(self):
        self.add("a.fastq")
        s = Sample.objects.get()
        r = self.client.post(self.url("dataset_remove"), {"s": [s.pk]})
        self.assertContains(r, "1 sample and their files removed")
        self.client.post(self.url("dataset_rename"), {"title": "New name"})
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.title, "New name")
        r = self.client.post(self.url("dataset_delete"))
        self.assertRedirects(r, reverse("srnatoolbox:data"))
        self.assertFalse(self.ds.directory().exists())
        r = self.client.get(self.ds.get_absolute_url())
        self.assertContains(r, "was deleted", status_code=410)
        self.assertEqual(self.client.post(self.url("dataset_links"), {"links": "x"}).status_code, 404)

    def test_selection_reaches_tool_page(self):
        self.add("a.fastq")
        s = Sample.objects.get()
        r = self.client.get(reverse("srnatoolbox:tool", args=["srnabench"]), {"dataset": self.ds.slug, "s": [s.pk]})
        self.assertContains(r, "1 sample</strong> of dataset")
        self.assertContains(r, "Start sRNAbench")
