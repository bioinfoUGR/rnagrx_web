import http.server
import ipaddress
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from jobs import fetch
from jobs.fetch import BadURL, check_url, normalize_share_link


class ShareLinkTests(SimpleTestCase):
    def test_dropbox(self):
        self.assertEqual(normalize_share_link("https://www.dropbox.com/scl/fi/abc/r.fq.gz?rlkey=xyz&dl=0"),
                         "https://www.dropbox.com/scl/fi/abc/r.fq.gz?rlkey=xyz&dl=1")

    def test_google_drive(self):
        expected = "https://drive.usercontent.google.com/download?id=1AbC_d-9&export=download&confirm=t"
        self.assertEqual(normalize_share_link("https://drive.google.com/file/d/1AbC_d-9/view?usp=sharing"), expected)
        self.assertEqual(normalize_share_link("https://drive.google.com/open?id=1AbC_d-9"), expected)

    def test_onedrive_and_nextcloud(self):
        self.assertTrue(normalize_share_link("https://1drv.ms/u/s!Abc").startswith(
            "https://api.onedrive.com/v1.0/shares/u!"))
        self.assertEqual(normalize_share_link("https://cloud.example.org/s/AbC123"),
                         "https://cloud.example.org/s/AbC123/download")
        self.assertEqual(normalize_share_link("https://example.org/data/r.fq.gz"), "https://example.org/data/r.fq.gz")


class CheckURLTests(SimpleTestCase):
    def test_internal_addresses_are_refused(self):
        for url in ["http://127.0.0.1/x.fq", "http://localhost:8100/dev/", "http://192.168.1.81/x",
                    "http://10.0.0.1/x", "http://169.254.169.254/latest/meta-data/", "http://[::1]/x",
                    "http://[::ffff:192.168.1.1]/x", "http://0.0.0.0/x"]:
            with self.subTest(url=url), self.assertRaisesMessage(BadURL, "not a public internet address"):
                check_url(url)

    def test_bad_links(self):
        for url, msg in [("file:///etc/passwd", "starting with"), ("javascript:alert(1)", "starting with"),
                         ("https://user:pw@example.org/x", "user name"), ("https:///x", "no server"),
                         ("https://no-such-host.invalid/x", "unknown")]:
            with self.subTest(url=url), self.assertRaisesMessage(BadURL, msg):
                check_url(url)


def _loopback_is_public(ip):
    """In tests the local test server (127.0.0.1) counts as public; everything else is checked as usual."""
    addr = ipaddress.ip_address(ip.split("%")[0])
    return addr.is_loopback and str(addr) == "127.0.0.1" or addr.is_global


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect-internal":
            self.send_response(302)
            self.send_header("Location", "http://10.1.2.3/secret")
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_error(404)
            return
        body = b"@r1\nACGT\n+\nIIII\n" * 1000
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", 'attachment; filename="sample 1.fastq"')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FetchTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        super().tearDownClass()

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(fetch, "_is_public", _loopback_is_public)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = []

    def test_download(self):
        path = fetch.fetch(f"{self.base}/data", self.dir, 10 ** 6, self.log.append)
        self.assertEqual(path.name, "sample_1.fastq")
        self.assertEqual(path.stat().st_size, 16000)

    def test_size_limit(self):
        with self.assertRaisesMessage(BadURL, "larger than"):
            fetch.fetch(f"{self.base}/data", self.dir, 1000, self.log.append)

    def test_redirect_to_internal_host_is_refused(self):
        with self.assertRaisesMessage(BadURL, "not a public internet address"):
            fetch.fetch(f"{self.base}/redirect-internal", self.dir, 10 ** 6, self.log.append)

    def test_http_error(self):
        with self.assertRaisesMessage(BadURL, "404"):
            fetch.fetch(f"{self.base}/missing", self.dir, 10 ** 6, self.log.append)
