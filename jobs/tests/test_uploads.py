import bz2
import gzip
import io
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job, Upload
from jobs.readfiles import NotReads, allowed_name, sniff
from jobs.uploads import attach_upload, claim_uploads, safe_name

FASTQ = b"@r1\nTGAGGTAGTAGGTTGTATAGTT\n+\nIIIIIIIIIIIIIIIIIIIIII\n@r2\nTAGCTTATCAGACTGATGTTGA\n+\nIIIIIIIIIIIIIIIIIIIIII\n"


class ReadFileTests(SimpleTestCase):
    def write(self, content: bytes) -> Path:
        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_formats(self):
        self.assertEqual(sniff(self.write(FASTQ)), "fastq")
        self.assertEqual(sniff(self.write(gzip.compress(FASTQ))), "fastq.gz")
        self.assertEqual(sniff(self.write(bz2.compress(FASTQ))), "fastq.bz2")
        self.assertEqual(sniff(self.write(b">1#10\nTGAGGTAGTAGG\n>2#3\nACGT\n")), "fasta")
        self.assertEqual(sniff(self.write(b"TGAGGTAGTAGG\t10\nACGT\t3\n")), "counts")

    def test_rejected(self):
        for content, msg in [(b"", "empty"), (b"<!DOCTYPE html><html>", "web page"), (b"\x00\xff\xfe", "not a text"),
                             (b"@r1\nACGT\n+\nII\n", "malformed"), (b">a\nhello world\n", "not a nucleotide"),
                             (b"col1\tcol2\n", "not FASTQ")]:
            with self.assertRaisesMessage(NotReads, msg):
                sniff(self.write(content))

    def test_names(self):
        self.assertTrue(allowed_name("S1_R1.fastq.gz"))
        self.assertTrue(allowed_name("reads.FA.bz2"))
        self.assertFalse(allowed_name("reads.bam"))
        self.assertEqual(safe_name("../../etc/pass wd.fq.gz"), "pass_wd.fq.gz")
        self.assertEqual(safe_name("C:\\data\\.hidden.fa"), "hidden.fa")


@override_settings(UPLOAD_CHUNK_MB=1)
class UploadTests(TestCase):
    def start(self, name="s1.fastq", size=len(FASTQ)):
        return self.client.post(reverse("jobs:upload_start"), {"name": name, "size": size})

    def put(self, token, offset, data):
        return self.client.put(reverse("jobs:upload_chunk", args=[token]) + f"?offset={offset}", data,
                               content_type="application/octet-stream")

    def test_full_upload_in_chunks_with_resume(self):
        r = self.start()
        self.assertEqual(r.status_code, 200)
        token = r.json()["token"]
        self.assertEqual(r.json()["chunk_size"], 1024 * 1024)
        self.assertEqual(self.put(token, 0, FASTQ[:30]).json(), {"received": 30})
        # a repeated or skipped chunk is refused with the current position, to resume from there
        r = self.put(token, 0, FASTQ[:30])
        self.assertEqual((r.status_code, r.json()["received"]), (409, 30))
        self.assertEqual(self.put(token, 50, FASTQ[50:]).status_code, 409)
        self.assertEqual(self.client.get(reverse("jobs:upload_status", args=[token])).json()["received"], 30)
        # finishing too early fails, then the rest arrives
        self.assertEqual(self.client.post(reverse("jobs:upload_finish", args=[token])).status_code, 409)
        self.assertEqual(self.put(token, 30, FASTQ[30:]).json()["received"], len(FASTQ))
        r = self.client.post(reverse("jobs:upload_finish", args=[token]))
        self.assertEqual(r.json()["status"], "complete")
        self.assertEqual(r.json()["format"], "fastq")
        u = Upload.objects.get(token=token)
        self.assertEqual(u.path().read_bytes(), FASTQ)

        # a job takes the file over
        request = self.client.get("/").wsgi_request
        uploads = claim_uploads(request, [token])
        job = Job.objects.create(app="srnatoolbox", tool="srnabench")
        dest = attach_upload(uploads[0], job, job.directory() / "input")
        self.assertEqual(dest.read_bytes(), FASTQ)
        u.refresh_from_db()
        self.assertEqual((u.status, u.job), (Upload.USED, job))
        self.assertFalse(u.directory().exists())
        with self.assertRaisesMessage(ValueError, "not complete or has expired"):
            claim_uploads(request, [token])

    def test_wrong_content_is_rejected_on_finish(self):
        token = self.start("x.fa", 11).json()["token"]
        self.put(token, 0, b"hello world")
        r = self.client.post(reverse("jobs:upload_finish", args=[token]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("not FASTQ", r.json()["message"])
        self.assertFalse(Upload.objects.get(token=token).directory().exists())

    def test_limits(self):
        self.assertIn("FASTQ, FASTA", self.start("x.bam").json()["error"])
        self.assertIn("empty", self.start(size=0).json()["error"])
        with self.settings(UPLOAD_MAX_GB=1):
            self.assertIn("larger than 1 GB", self.start(size=2 * 1024 ** 3).json()["error"])
        with self.settings(UPLOAD_SESSION_MAX_GB=1, UPLOAD_MAX_GB=1):
            self.assertEqual(self.start(size=600 * 1024 ** 2).status_code, 200)
            self.assertIn("would exceed", self.start(size=600 * 1024 ** 2).json()["error"])
        with self.settings(SCRATCH_MIN_FREE_GB=10 ** 9):
            self.assertEqual(self.start().status_code, 503)
        token = self.start().json()["token"]
        self.assertEqual(self.put(token, 0, b"x" * (1024 * 1024 + 1)).status_code, 400)  # chunk too large
        self.assertEqual(self.put(token, 0, FASTQ + b"extra").status_code, 400)  # beyond the announced size

    def test_other_sessions_cannot_touch_an_upload(self):
        token = self.start().json()["token"]
        self.client.cookies.clear()
        self.assertEqual(self.put(token, 0, FASTQ).status_code, 404)
        self.assertEqual(self.client.get(reverse("jobs:upload_status", args=[token])).status_code, 404)
        with self.assertRaisesMessage(ValueError, "another browser session"):
            claim_uploads(self.client.get("/").wsgi_request, [token])

    def test_cancel(self):
        token = self.start().json()["token"]
        r = self.client.post(reverse("jobs:upload_cancel", args=[token]))
        self.assertEqual(r.json()["status"], "failed")
        self.assertFalse(Upload.objects.get(token=token).directory().exists())

    def test_cleanup_removes_unused_uploads(self):
        token = self.start().json()["token"]
        u = Upload.objects.get(token=token)
        fresh = Upload.objects.get(token=self.start().json()["token"])
        Upload.objects.filter(pk=u.pk).update(updated_at=timezone.now() - timedelta(hours=49))
        call_command("jobs_cleanup", stdout=io.StringIO())
        self.assertFalse(Upload.objects.filter(pk=u.pk).exists())
        self.assertFalse(u.directory().exists())
        self.assertTrue(fresh.directory().exists())
