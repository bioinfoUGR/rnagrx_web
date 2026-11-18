import gzip
import io
import os
import shutil
import tarfile
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs import datasets, samplesheet
from jobs.datasets import (DatasetError, add_links, create_dataset, edit_cells, remove_samples,
                           sample_name_from_file)
from jobs.fetch import BadURL
from jobs.models import DataFile, Dataset, Job, Sample, Upload

FASTQ = b"@r1\nTGAGGTAGTAGGTTGTATAGTT\n+\nIIIIIIIIIIIIIIIIIIIIII\n@r2\nTAGCTTATCAGACTGATGTTGA\n+\nIIIIIIIIIIIIIIIIIIIIII\n"


def tar_bytes(members: dict, mode="w:gz", extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for info in extra or []:
            tf.addfile(info)
    return buf.getvalue()


def zip_bytes(members: dict, symlink: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


class Base(TestCase):
    def setUp(self):
        self.ds = create_dataset("srnatoolbox", "sess")

    def tearDown(self):
        shutil.rmtree(self.ds.directory(), ignore_errors=True)

    def upload(self, name, data, ds=None):
        """Upload through the HTTP endpoints, in one chunk; returns the finish response."""
        ds = ds or self.ds
        r = self.client.post(reverse("jobs:upload_start"), {"name": name, "size": len(data), "dataset": ds.slug})
        self.assertEqual(r.status_code, 200, r.content)
        token = r.json()["token"]
        r = self.client.put(reverse("jobs:upload_chunk", args=[token]) + "?offset=0", data,
                            content_type="application/octet-stream")
        self.assertEqual(r.status_code, 200, r.content)
        return self.client.post(reverse("jobs:upload_finish", args=[token]))


class NameTests(TestCase):
    def test_sample_names(self):
        for name, expected in [("CRC_001_S1_R1_001.fastq.gz", ("CRC_001", "")),
                               ("CRC_045_S45_L002_R1_001.fastq.gz", ("CRC_045", "_L002")),
                               ("tumour 1.fq", ("tumour_1", "")), ("sample_001.fa.bz2", ("sample_001", "")),
                               ("..hidden.fastq", ("hidden", "")), ("___.fastq", ("sample", ""))]:
            self.assertEqual(sample_name_from_file(name), expected, name)


class UploadIntoDatasetTests(Base):
    def test_upload_becomes_file_and_sample(self):
        r = self.upload("CRC_001_S1_R1_001.fastq", FASTQ)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["file_status"], "ready")
        df = DataFile.objects.get()
        self.assertEqual((df.name, df.status, df.file_format, df.size), ("CRC_001_S1_R1_001.fastq", "ready", "fastq",
                                                                         len(FASTQ)))
        self.assertTrue(df.path().is_file())
        self.assertEqual(df.path().parent, self.ds.files_dir())
        s = Sample.objects.get()
        self.assertEqual((s.name, s.files), ("CRC_001", [df.tag]))
        u = Upload.objects.get()
        self.assertEqual(u.status, Upload.USED)
        self.assertFalse(u.directory().exists())

    def test_same_file_again_continues_the_upload(self):
        url = reverse("jobs:upload_start")
        args = {"name": "a.fastq", "size": len(FASTQ), "dataset": self.ds.slug}
        token = self.client.post(url, args).json()["token"]
        self.client.put(reverse("jobs:upload_chunk", args=[token]) + "?offset=0", FASTQ[:10],
                        content_type="application/octet-stream")
        r = self.client.post(url, args).json()
        self.assertEqual((r["token"], r["received"]), (token, 10))
        # another browser starts a new upload
        other = self.client_class()
        self.assertNotEqual(other.post(url, args).json()["token"], token)

    def test_lanes_go_into_one_sample_and_names_stay_unique(self):
        self.upload("CRC_045_S45_L001_R1_001.fastq", FASTQ)
        self.upload("CRC_045_S45_L002_R1_001.fastq", FASTQ)
        self.upload("x.fastq", FASTQ)
        self.upload("x.fastq", FASTQ)
        samples = {s.name: s for s in Sample.objects.all()}
        self.assertEqual(sorted(samples), ["CRC_045", "x", "x_2"])
        self.assertEqual(len(samples["CRC_045"].files), 2)
        self.assertEqual(sorted(DataFile.objects.values_list("name", flat=True)),
                         ["CRC_045_S45_L001_R1_001.fastq", "CRC_045_S45_L002_R1_001.fastq", "x.fastq", "x_2.fastq"])

    def test_not_reads_refused(self):
        r = self.upload("notes.txt", b"just some text\n")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(DataFile.objects.exists())
        r = self.client.post(reverse("jobs:upload_start"), {"name": "notes.docx", "size": 5, "dataset": self.ds.slug})
        self.assertContains(r, "archives", status_code=400)

    def test_dataset_quota(self):
        with mock.patch.object(datasets, "GB", 100), override_settings(DATASET_MAX_GB=1):
            r = self.client.post(reverse("jobs:upload_start"),
                                 {"name": "big.fastq", "size": 101, "dataset": self.ds.slug})
        self.assertContains(r, "limit per dataset", status_code=400)

    def test_session_quota(self):
        other = create_dataset("srnatoolbox", "sess")
        DataFile.objects.create(dataset=other, name="a.fq", size=90, source="upload", status="ready")
        with mock.patch.object(datasets, "GB", 100), override_settings(DATASET_SESSION_MAX_GB=1):
            with self.assertRaisesMessage(DatasetError, "started from this browser"):
                datasets.check_new_bytes(self.ds, 20)
            datasets.check_new_bytes(self.ds, 5)


class ArchiveTests(Base):
    def files(self):
        return sorted(DataFile.objects.filter(status="ready").values_list("name", flat=True))

    def test_tar_gz_with_folders_links_and_other_files(self):
        link = tarfile.TarInfo("batch/link.fastq")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        data = tar_bytes({"batch/S1.fastq.gz": gzip.compress(FASTQ), "batch/sub/S2.fq": FASTQ,
                          "../../escape.fastq": FASTQ, "batch/README.md": b"hello", "batch/md5.txt": b"abc  S1\n",
                          "batch/._S1.fastq.gz": b"\x00\x05", "__MACOSX/batch/S3.fq": FASTQ}, extra=[link])
        r = self.upload("batch.tar.gz", data)
        self.assertEqual(r.json()["file_status"], "unpacking")  # eager Celery: already done in the test
        self.assertEqual(self.files(), ["S1.fastq.gz", "S2.fq", "escape.fastq"])
        archive = DataFile.objects.get(is_archive=True)
        self.assertEqual(archive.status, "unpacked")
        self.assertIn("3 read files added", archive.message)
        self.assertIn("README.md", archive.message)
        self.assertIn("md5.txt", archive.message)
        self.assertFalse(archive.incoming().exists())
        on_disk = sorted(p.name for p in self.ds.directory().rglob("*") if p.is_file())
        self.assertEqual(len(on_disk), 3)
        self.assertTrue(all(p.resolve().is_relative_to(self.ds.files_dir()) for p in self.ds.directory().rglob("*")
                            if p.is_file()))
        self.assertEqual(sorted(Sample.objects.values_list("name", flat=True)), ["S1", "S2", "escape"])

    def test_zip_with_symlink_and_duplicates(self):
        data = zip_bytes({"a/S1.fastq": FASTQ, "b/S1.fastq": FASTQ, "../evil.fa": b">1\nACGT\n"},
                         symlink="a/S9.fastq")
        self.upload("run.zip", data)
        self.assertEqual(self.files(), ["S1.fastq", "S1_2.fastq", "evil.fa"])

    def test_size_guard(self):
        data = tar_bytes({f"S{i}.fastq": FASTQ * 20 for i in range(5)})
        with mock.patch.object(datasets, "GB", 5000), override_settings(DATASET_MAX_GB=1):
            self.upload("big.tar.gz", data)
        archive = DataFile.objects.get(is_archive=True)
        self.assertEqual(archive.status, "failed")
        self.assertIn("limit per dataset", archive.message)
        self.assertIn("read files were added before", archive.message)
        self.assertFalse(archive.incoming().exists())

    def test_member_count_guard_and_no_reads(self):
        with override_settings(ARCHIVE_MAX_MEMBERS=3):
            self.upload("many.tar.gz", tar_bytes({f"S{i}.fq": FASTQ for i in range(5)}))
        self.assertIn("more than 3 files", DataFile.objects.get(is_archive=True).message)
        DataFile.objects.all().delete()
        self.upload("docs.zip", zip_bytes({"a.txt": b"hello", "b.csv": b"x,y\n"}))
        archive = DataFile.objects.get(is_archive=True)
        self.assertEqual(archive.status, "failed")
        self.assertIn("no read files", archive.message)

    def test_corrupt_archive(self):
        data = tar_bytes({"S1.fq": FASTQ * 50}, mode="w")
        self.upload("broken.tar", data[:1000])  # header complete, member data cut off
        archive = DataFile.objects.get(is_archive=True)
        self.assertEqual(archive.status, "failed")
        self.assertIn("cannot be read", archive.message)
        r = self.upload("broken2.tar.gz", b"\x1f\x8b" + b"x" * 100)
        self.assertEqual(r.status_code, 400)  # neither an archive nor reads


class LinkTests(Base):
    def fake_fetch(self, content: bytes, name: str):
        def fetch(url, dest_dir, max_bytes, log, name_=None):
            path = Path(dest_dir) / name
            path.write_bytes(content)
            return path
        return mock.patch("jobs.fetch.fetch", side_effect=fetch)

    def test_link_to_reads_and_archive(self):
        with self.fake_fetch(gzip.compress(FASTQ), "S7.fastq.gz"):
            files, errors = add_links(self.ds, "https://example.org/S7.fastq.gz\n\nftp://ftp.sra.ebi.ac.uk/x/S8.fq.gz")
        self.assertEqual((len(files), errors), (2, []))
        self.assertEqual(sorted(DataFile.objects.values_list("status", flat=True)), ["ready", "ready"])
        DataFile.objects.all().delete()
        Sample.objects.all().delete()
        with self.fake_fetch(tar_bytes({"A.fq": FASTQ, "B.fq": FASTQ}), "batch.tgz"):
            add_links(self.ds, "https://www.dropbox.com/scl/fi/abc/batch.tgz?dl=0")
        self.assertEqual(sorted(DataFile.objects.filter(status="ready").values_list("name", flat=True)),
                         ["A.fq", "B.fq"])
        self.assertEqual(DataFile.objects.get(is_archive=True).status, "unpacked")

    def test_failures(self):
        files, errors = add_links(self.ds, "file:///etc/passwd\njavascript:alert(1)")
        self.assertEqual(len(files), 0)
        self.assertEqual(len(errors), 2)
        _, errors = add_links(self.ds, "\n".join(f"https://e.org/{i}.fq" for i in range(201)))
        self.assertIn("at most", errors[0])
        with mock.patch("jobs.fetch.fetch", side_effect=BadURL("“10.0.0.1” is not a public internet address.")):
            add_links(self.ds, "http://10.0.0.1/x.fq")
        df = DataFile.objects.get()
        self.assertEqual(df.status, "failed")
        self.assertIn("not a public", df.message)
        with self.fake_fetch(b"<!DOCTYPE html><html>login</html>", "x.fq"):
            add_links(self.ds, "https://drive.google.com/file/d/abc/view")
        self.assertIn("web page", DataFile.objects.order_by("-id").first().message)
        self.assertFalse(any(p.is_file() for p in self.ds.incoming_dir().rglob("*")))


class SampleSheetTests(Base):
    def setUp(self):
        super().setUp()
        for name in ["CTL_1.fastq", "CRC_1_L001.fastq", "CRC_1_L002.fastq", "extra.fastq"]:
            p = self.ds.directory() / name
            p.write_bytes(FASTQ)
            datasets.register_file(self.ds, p, name, DataFile.UPLOAD, file_format="fastq")

    def test_export_formats(self):
        datasets.add_column(self.ds, "group")
        s = self.ds.samples.get(name="CTL_1")
        edit_cells(self.ds, [{"sample": s.pk, "field": "column", "column": "group", "value": "=HYPERLINK(1)"}])
        tsv = samplesheet.export(self.ds, "tsv").decode()
        self.assertTrue(tsv.startswith("file\tname\tgroup\n"))
        self.assertIn("CRC_1_L001.fastq; CRC_1_L002.fastq\tCRC_1\t\n", tsv)
        self.assertIn("'=HYPERLINK(1)", tsv)
        self.assertIn("file,name,group", samplesheet.export(self.ds, "csv").decode())
        rows = samplesheet.parse(samplesheet.export(self.ds, "xlsx"), "s.xlsx")
        self.assertEqual(rows[0], ["file", "name", "group"])
        self.assertEqual(len(rows), 4)

    def test_apply_sheet(self):
        text = ("file,name,group,age\n"
                "CTL_1.fastq,ctrl_a,control,51\n"
                "C:\\data\\CRC_1_L001.fastq ; CRC_1_L002.fastq,tum_a,tumour,62.0\n")
        p = samplesheet.plan(self.ds, samplesheet.parse(text.encode(), "s.csv"))
        self.assertEqual(p["n_errors"], 0, p["errors"])
        self.assertEqual(p["unlisted"], ["extra.fastq"])
        samplesheet.apply(self.ds, p)
        self.ds.refresh_from_db()
        self.assertEqual(self.ds.columns, ["group", "age"])
        samples = {s.name: s for s in self.ds.samples.all()}
        self.assertEqual(sorted(samples), ["ctrl_a", "extra", "tum_a"])
        self.assertEqual(len(samples["tum_a"].files), 2)
        self.assertEqual(samples["tum_a"].annotations, {"group": "tumour", "age": "62.0"})

    def test_xlsx_numbers(self):
        from openpyxl import Workbook
        wb = Workbook()
        wb.active.append(["file", "name", "batch"])
        wb.active.append(["CTL_1.fastq", "a", 1.0])
        buf = io.BytesIO()
        wb.save(buf)
        rows = samplesheet.parse(buf.getvalue(), "s.xlsx")
        self.assertEqual(rows[1], ["CTL_1.fastq", "a", "1"])

    def test_errors(self):
        def errors(text):
            return [m for _, m in samplesheet.plan(self.ds, samplesheet.parse(text.encode(), "s.tsv"))["errors"]]
        with self.assertRaisesMessage(samplesheet.SheetError, "“file” and “name”"):
            errors("sample\tfile\n")
        with self.assertRaisesMessage(samplesheet.SheetError, "already a column"):
            errors("file\tname\tg\tg\n")
        msgs = errors("file\tname\n"
                      "nope.fastq\ta\n"
                      "CTL_1.fastq\tb c\n"
                      "CRC_1_L001.fastq\tx\n"
                      "CRC_1_L001.fastq\ty\n"
                      "CRC_1_L002.fastq\tx\n")
        self.assertIn("not a file of this dataset", msgs[0])
        self.assertIn("up to 64 letters", msgs[1])
        self.assertIn("already used in row 4", msgs[2])
        self.assertIn("used twice", msgs[3])
        with self.assertRaises(samplesheet.SheetError):
            samplesheet.apply(self.ds, {"n_errors": 1})
        with self.assertRaisesMessage(samplesheet.SheetError, ".xls"):
            samplesheet.parse(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "old.xls")


class EditTests(Base):
    def setUp(self):
        super().setUp()
        for name in ["a.fastq", "b.fastq"]:
            p = self.ds.directory() / name
            p.write_bytes(FASTQ)
            datasets.register_file(self.ds, p, name, DataFile.UPLOAD, file_format="fastq")
        self.a, self.b = self.ds.samples.all()

    def test_edit_cells(self):
        datasets.add_column(self.ds, "group")
        errors = edit_cells(self.ds, [
            {"sample": self.a.pk, "field": "name", "value": "b"},
            {"sample": self.a.pk, "field": "name", "value": "bad name"},
            {"sample": self.b.pk, "field": "name", "value": "B1"},
            {"sample": self.a.pk, "field": "column", "column": "group", "value": "  tumour \t x "},
            {"sample": self.a.pk, "field": "column", "column": "nope", "value": "x"},
            {"sample": 999999, "field": "name", "value": "z"}])
        self.assertEqual(sorted(errors), sorted(["999999:name", f"{self.a.pk}:column:nope", f"{self.a.pk}:name"]))
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.name, self.b.name, self.a.annotations), ("a", "B1", {"group": "tumour x"}))
        datasets.remove_column(self.ds, "group")
        self.a.refresh_from_db()
        self.assertEqual(self.a.annotations, {})
        with self.assertRaises(DatasetError):
            datasets.add_column(self.ds, "Name")

    def test_remove_samples(self):
        path = DataFile.objects.get(tag=self.a.files[0]).path()
        job = Job.objects.create(app="srnatoolbox", tool="srnabench", inputs={"dataset": self.ds.slug})
        with self.assertRaises(DatasetError):
            remove_samples(self.ds, [self.a.pk])
        job.status = Job.DONE
        job.save()
        self.assertEqual(remove_samples(self.ds, [self.a.pk]), 1)
        self.assertFalse(path.exists())
        self.assertEqual(DataFile.objects.count(), 1)


class CleanupTests(Base):
    def test_expiry(self):
        old = timezone.now() - timedelta(days=settings.DATASET_MAX_AGE_DAYS + 1)
        DataFile.objects.create(dataset=self.ds, name="a.fq", source="upload", status="ready")
        Dataset.objects.filter(pk=self.ds.pk).update(last_used_at=old)
        busy = create_dataset("srnatoolbox", "s2")
        DataFile.objects.create(dataset=busy, name="a.fq", source="upload", status="ready")
        Dataset.objects.filter(pk=busy.pk).update(last_used_at=old)
        Job.objects.create(app="srnatoolbox", tool="srnabench", inputs={"dataset": busy.slug})
        empty = create_dataset("srnatoolbox", "s3")
        Dataset.objects.filter(pk=empty.pk).update(created_at=timezone.now() - timedelta(days=3))
        fresh = create_dataset("srnatoolbox", "s4")
        stalled = DataFile.objects.create(dataset=fresh, name="x", source="link", status="transferring")
        DataFile.objects.filter(pk=stalled.pk).update(updated_at=timezone.now() - timedelta(days=2))

        call_command("jobs_cleanup", stdout=io.StringIO())
        deleted = set(Dataset.objects.filter(deleted_at__isnull=False).values_list("slug", flat=True))
        self.assertEqual(deleted, {self.ds.slug, empty.slug})
        self.assertFalse(self.ds.directory().exists())
        self.assertFalse(DataFile.objects.filter(dataset=self.ds).exists())
        stalled.refresh_from_db()
        self.assertEqual(stalled.status, "failed")
        for d in (busy, empty, fresh):
            shutil.rmtree(d.directory(), ignore_errors=True)
