"""Downloading input files from links given by the user (http, https, ftp), including cloud share links.

The server sits in an internal network, so a link must never reach internal hosts (SSRF): every connection,
including those after redirects, goes only to public IP addresses. Sizes are capped while downloading.
"""
from __future__ import annotations

import base64
import ftplib
import http.client
import ipaddress
import re
import socket
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit

from .uploads import safe_name

USER_AGENT = "sRNAtoolbox-fetch/1.0"
TIMEOUT = 60  # seconds without data
BLOCK = 1024 * 1024
SCHEMES = {"http", "https", "ftp"}


class BadURL(ValueError):
    """The link cannot be used; the message is meant for the user."""


# ---------------------------------------------------------------------------
# share links of cloud storage -> direct download links

def normalize_share_link(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query, keep_blank_values=True)

    if host in ("www.dropbox.com", "dropbox.com"):  # ...?dl=0 -> dl=1 (keeps rlkey of /scl/fi/ links)
        query["dl"] = ["1"]
        return urlunsplit(("https", "www.dropbox.com", parts.path, urlencode(query, doseq=True), ""))

    if host in ("drive.google.com", "docs.google.com"):
        m = re.search(r"/file/d/([A-Za-z0-9_-]+)", parts.path)
        file_id = m.group(1) if m else (query.get("id") or [""])[0]
        if file_id:  # direct download, skipping the virus-scan page of large files
            return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"

    if host in ("1drv.ms", "onedrive.live.com") and "download" not in parts.path:
        encoded = base64.urlsafe_b64encode(url.strip().encode()).decode().rstrip("=")
        return f"https://api.onedrive.com/v1.0/shares/u!{encoded}/root/content"

    # Nextcloud / ownCloud public shares: https://host/s/<token> or /index.php/s/<token>
    if re.fullmatch(r"(/index\.php)?/s/[A-Za-z0-9]+/?", parts.path):
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") + "/download", "", ""))

    return url.strip()


# ---------------------------------------------------------------------------
# only public addresses

def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def public_addresses(host: str, port: int) -> list[tuple]:
    """getaddrinfo results for host:port; BadURL if the host is unknown or any address is not public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise BadURL(f"The server “{host}” is unknown.")
    if not infos or not all(_is_public(info[4][0]) for info in infos):
        raise BadURL(f"“{host}” is not a public internet address.")
    return infos


def check_url(url: str) -> str:
    """The normalised link, after checking scheme and host; raises BadURL."""
    url = normalize_share_link(url)
    parts = urlsplit(url)
    if parts.scheme.lower() not in SCHEMES:
        raise BadURL("Please give a link starting with https://, http:// or ftp://.")
    if not parts.hostname:
        raise BadURL("The link has no server name.")
    if parts.username or parts.password:
        raise BadURL("Links with a user name or password are not accepted.")
    default = {"http": 80, "https": 443, "ftp": 21}[parts.scheme.lower()]
    try:
        port = parts.port or default
    except ValueError:
        raise BadURL("The link has an invalid port.")
    public_addresses(parts.hostname, port)
    return url


def _safe_create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    """socket.create_connection that connects only to the public addresses of the host."""
    host, port = address
    last = None
    for family, socktype, proto, _, sockaddr in public_addresses(host, port):
        sock = socket.socket(family, socktype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise last or OSError(f"cannot connect to {host}")


class _SafeHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_create_connection


class _SafeHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _safe_create_connection


class _SafeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_SafeHTTPConnection, req)


class _SafeHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_SafeHTTPSConnection, req, context=self._context)


class _NoFTPHandler(urllib.request.FTPHandler):
    def ftp_open(self, req):
        raise BadURL("The link redirects to an FTP server; please give the FTP link directly.")


def _opener():
    # no proxies from the environment; the safe handlers replace urllib's defaults
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SafeHTTPHandler, _SafeHTTPSHandler,
                                       _NoFTPHandler)


# ---------------------------------------------------------------------------
# download

def _copy(src, dest: Path, max_bytes: int, log, label: str) -> int:
    done, next_report = 0, 1024 ** 3
    with open(dest, "wb") as out:
        while True:
            block = src.read(BLOCK)
            if not block:
                break
            done += len(block)
            if done > max_bytes:
                raise BadURL(f"{label}: the file is larger than {max_bytes // 1024 ** 3} GB, the limit per file.")
            out.write(block)
            if done >= next_report:
                log(f"{label}: {done / 1024 ** 3:.1f} GB downloaded")
                next_report += 1024 ** 3
    return done


def _filename(url: str, header_name: str | None) -> str:
    name = header_name or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    return safe_name(name) if name and name not in ("download", "content") else "reads"


def fetch(url: str, dest_dir: Path, max_bytes: int, log, name: str | None = None) -> Path:
    """Download the file of a link into dest_dir; returns its path. Raises BadURL with a message for the user."""
    url = check_url(url)
    parts = urlsplit(url)
    dest_dir.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    if parts.scheme.lower() == "ftp":
        return _fetch_ftp(parts, dest_dir, max_bytes, log, name)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _opener().open(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise BadURL(f"The file is larger than {max_bytes // 1024 ** 3} GB, the limit per file.")
            dest = dest_dir / (name or _filename(resp.url, resp.headers.get_filename()))
            size = _copy(resp, dest, max_bytes, log, dest.name)
    except urllib.error.HTTPError as exc:
        raise BadURL(f"The server answered {exc.code} {exc.reason} for {url}.")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, BadURL):
            raise reason
        raise BadURL(f"The link could not be opened: {reason}.")
    except (TimeoutError, socket.timeout):
        raise BadURL("The server stopped sending data.")
    log(f"{dest.name}: {size:,} bytes")
    return dest


def _fetch_ftp(parts, dest_dir: Path, max_bytes: int, log, name: str | None) -> Path:
    # connect to a checked public IP; FTP has no host header, and data connections go to the same host
    infos = public_addresses(parts.hostname, parts.port or 21)
    ip = infos[0][4][0]
    path = unquote(parts.path)
    dest = dest_dir / (name or _filename(path, None))
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(ip, parts.port or 21, timeout=TIMEOUT)
            ftp.login()
            size = ftp.size(path)
            if size and size > max_bytes:
                raise BadURL(f"The file is larger than {max_bytes // 1024 ** 3} GB, the limit per file.")
            conn = ftp.transfercmd(f"RETR {path}")
            with conn, conn.makefile("rb") as src:
                done = _copy(src, dest, max_bytes, log, dest.name)
            ftp.voidresp()
    except ftplib.all_errors as exc:
        if isinstance(exc, BadURL):
            raise
        raise BadURL(f"The FTP download failed: {exc}")
    log(f"{dest.name}: {done:,} bytes")
    return dest
