"""Chunked, resumable upload of large read files from the browser.

1. POST  start/            name, size            -> {token, chunk_size, received}
2. PUT   <token>/chunk/?offset=N   raw bytes      -> {received}   (offset must equal the bytes received so far)
3. GET   <token>/                                 -> {received, size, status, ...}  (to resume)
4. POST  <token>/finish/                          -> {status, format}  (size and file format are checked)
   POST  <token>/cancel/
Chunks are small enough (UPLOAD_CHUNK_MB) for the body limits of Apache and Django. Only the browser session that
started an upload can continue or use it. A job takes completed uploads with `claim_uploads` and `attach_upload`.
"""
from __future__ import annotations

import os
import re
import shutil

from django.conf import settings
from django.db.models import Sum
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import Upload
from .readfiles import NotReads, allowed_name, sniff

SESSION_UPLOADS = "uploads"
SESSION_UPLOADS_MAX = 500
COPY_BLOCK = 1024 * 1024
GB = 1024 ** 3


def _error(message: str, status: int = 400, **extra) -> JsonResponse:
    return JsonResponse({"error": message, **extra}, status=status)


def _describe(u: Upload) -> dict:
    return {"token": u.token, "filename": u.filename, "size": u.size, "received": u.received, "status": u.status,
            "format": u.file_format, "message": u.message}


def safe_name(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")[-120:]
    return base or "reads"


def _session_tokens(request) -> list[str]:
    return request.session.get(SESSION_UPLOADS, [])


def _own(request, token: str) -> Upload:
    if token not in _session_tokens(request):
        raise Http404
    try:
        return Upload.objects.get(token=token)
    except Upload.DoesNotExist:
        raise Http404


def _remove_files(u: Upload) -> None:
    shutil.rmtree(u.directory(), ignore_errors=True)


@require_POST
def start(request):
    name = (request.POST.get("name") or "").strip()
    try:
        size = int(request.POST.get("size", ""))
    except ValueError:
        return _error("The file size is missing.")
    if not name:
        return _error("The file name is missing.")
    if not allowed_name(name):
        return _error(f"{name}: please upload FASTQ, FASTA or read/count files (.fastq, .fq, .fa, .fasta, .txt, "
                      ".tsv, .csv), optionally compressed with gzip (.gz) or bzip2 (.bz2).")
    if size <= 0:
        return _error(f"{name} is empty.")
    max_file = settings.UPLOAD_MAX_GB * GB
    if size > max_file:
        return _error(f"{name} is larger than {settings.UPLOAD_MAX_GB} GB, the limit per file. Compressing it with "
                      "gzip usually reduces FASTQ files to a quarter.")
    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    pending = Upload.objects.filter(session_key=session_key, status__in=[Upload.UPLOADING, Upload.COMPLETE]) \
        .aggregate(total=Sum("size"))["total"] or 0
    if pending + size > settings.UPLOAD_SESSION_MAX_GB * GB:
        return _error(f"Your uploads waiting for a job would exceed {settings.UPLOAD_SESSION_MAX_GB} GB. "
                      "Start a job with the files already uploaded, or remove some of them.")
    upload_dir = settings.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(upload_dir).free - size < settings.SCRATCH_MIN_FREE_GB * GB:
        return _error("The server is short of disk space at the moment. Please try again later.", status=503)

    u = Upload.objects.create(session_key=session_key, filename=name[:255], stored_name=safe_name(name), size=size)
    u.directory().mkdir(parents=True)
    u.path().touch()
    request.session[SESSION_UPLOADS] = (_session_tokens(request) + [u.token])[-SESSION_UPLOADS_MAX:]
    return JsonResponse({**_describe(u), "chunk_size": settings.UPLOAD_CHUNK_MB * 1024 * 1024})


@require_http_methods(["PUT"])
def chunk(request, token):
    u = _own(request, token)
    if u.status != Upload.UPLOADING:
        return _error("This upload is not open any more.", status=409, received=u.received)
    try:
        offset = int(request.GET.get("offset", ""))
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return _error("Bad offset or length.")
    if offset != u.received:
        return _error("Unexpected offset; continue from the bytes received.", status=409, received=u.received)
    max_chunk = settings.UPLOAD_CHUNK_MB * 1024 * 1024
    if length <= 0 or length > max_chunk or offset + length > u.size:
        return _error("Bad chunk length.", received=u.received)

    written = 0
    with open(u.path(), "r+b") as fh:
        fh.seek(offset)
        while written < length:
            block = request.read(min(COPY_BLOCK, length - written))
            if not block:
                break
            fh.write(block)
            written += len(block)
        fh.truncate()
    if written != length:
        return _error("The chunk arrived incomplete; please send it again.", received=u.received)
    # only one request can move `received` forward from this offset
    if not Upload.objects.filter(pk=u.pk, received=offset, status=Upload.UPLOADING).update(received=offset + written):
        u.refresh_from_db()
        return _error("Another request changed this upload.", status=409, received=u.received)
    return JsonResponse({"received": offset + written})


@require_GET
def status(request, token):
    return JsonResponse(_describe(_own(request, token)))


@require_POST
def finish(request, token):
    u = _own(request, token)
    if u.status != Upload.UPLOADING:
        return JsonResponse(_describe(u))
    on_disk = u.path().stat().st_size if u.path().exists() else -1
    if u.received != u.size or on_disk != u.size:
        return _error(f"The upload is incomplete ({u.received:,} of {u.size:,} bytes).", status=409,
                      received=u.received)
    try:
        u.file_format = sniff(u.path())
        u.status = Upload.COMPLETE
    except NotReads as exc:
        u.status = Upload.FAILED
        u.message = f"{u.filename}: {exc}"[:500]
        _remove_files(u)
    u.save(update_fields=["status", "file_format", "message", "updated_at"])
    return JsonResponse(_describe(u), status=200 if u.status == Upload.COMPLETE else 400)


@require_POST
def cancel(request, token):
    u = _own(request, token)
    if u.status in (Upload.UPLOADING, Upload.COMPLETE):
        _remove_files(u)
        u.status = Upload.FAILED
        u.message = "cancelled"
        u.save(update_fields=["status", "message", "updated_at"])
    return JsonResponse(_describe(u))


def claim_uploads(request, tokens) -> list[Upload]:
    """Completed uploads of this session, in the given order; raises ValueError (message for the user)."""
    out = []
    for token in tokens:
        if token not in _session_tokens(request):
            raise ValueError("An uploaded file belongs to another browser session. Please upload it again.")
        u = Upload.objects.filter(token=token).first()
        if u is None or u.status != Upload.COMPLETE or not u.path().is_file():
            raise ValueError(f"The upload of {u.filename if u else 'a file'} is not complete or has expired. "
                             "Please upload it again.")
        out.append(u)
    return out


def attach_upload(u: Upload, job, dest_dir, name: str | None = None):
    """Move an uploaded file into a job's directory (same file system) and mark it used."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name or u.stored_name)
    os.replace(u.path(), dest)
    _remove_files(u)
    u.status = Upload.USED
    u.job = job
    u.save(update_fields=["status", "job", "updated_at"])
    return dest
