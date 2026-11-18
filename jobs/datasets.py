"""Datasets: read files uploaded or linked once and used by several jobs.

A file reaches a dataset in one of three ways:
- browser upload (jobs/uploads.py): on `finish`, `add_upload` moves it into the dataset;
- link: `add_links` creates a DataFile per link, the task `fetch_link` (queue TRANSFER_QUEUE) downloads it;
- archive (ZIP or tar, uploaded or linked): the task `unpack_archive` reads it member by member.
Every read file becomes a DataFile (status ready) and gets a Sample named after the file; lanes of one sample
(..._L001_..., ..._L002_...) are put into the same sample. Sample sheets (jobs/samplesheet.py) change samples
and annotations afterwards.

Archives are never extracted with extractall: only regular files are read, folders are dropped from the names,
and sizes are counted while copying (the sizes in an archive's index can lie).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import DataFile, Dataset, Job, Sample, Upload
from .readfiles import NotReads, allowed_name, archive_name, is_archive, safe_name, sniff, strip_suffixes

log = logging.getLogger(__name__)

GB = 1024 ** 3
BLOCK = 1024 * 1024
MAX_LINKS = 200
SKIPPED_SHOWN = 5
# Illumina style names: <sample>_S<n>_L<lane>_R1_001
ILLUMINA_RE = re.compile(r"^(?P<base>.+?)(?P<s>_S\d+)?(?P<lane>_L\d{3})?(?P<read>_R1(_001)?)?$")


class DatasetError(ValueError):
    """Something the user has to change; the message is meant for the user."""


# ---------------------------------------------------------------------------
# datasets

def create_dataset(app: str, session_key: str, title: str = "") -> Dataset:
    title = title.strip()[:200] or f"Dataset of {timezone.localtime():%-d %B %Y, %H:%M}"
    ds = Dataset.objects.create(app=app, session_key=session_key, title=title)
    ds.files_dir().mkdir(parents=True, exist_ok=True)
    return ds


def active_jobs(ds: Dataset):
    return Job.objects.filter(inputs__dataset=ds.slug, status__in=[Job.QUEUED, Job.RUNNING])


def delete_dataset(ds: Dataset) -> None:
    shutil.rmtree(ds.directory(), ignore_errors=True)
    ds.samples.all().delete()
    ds.files.all().delete()
    for u in ds.uploads.all():
        shutil.rmtree(u.directory(), ignore_errors=True)
    ds.uploads.all().delete()
    ds.deleted_at = timezone.now()
    ds.save(update_fields=["deleted_at"])


def _dir_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def used_bytes(ds: Dataset) -> int:
    """Bytes the dataset holds or is about to hold (ready files, uploads and downloads in progress)."""
    ready = ds.files.filter(status=DataFile.READY).aggregate(s=Sum("size"))["s"] or 0
    uploads = ds.uploads.filter(status__in=[Upload.UPLOADING, Upload.COMPLETE]).aggregate(s=Sum("size"))["s"] or 0
    return ready + uploads + _dir_bytes(ds.incoming_dir())


def ready_bytes(ds: Dataset) -> int:
    return ds.files.filter(status=DataFile.READY).aggregate(s=Sum("size"))["s"] or 0


def quota_left(ds: Dataset) -> int:
    return settings.DATASET_MAX_GB * GB - used_bytes(ds)


def check_new_bytes(ds: Dataset, size: int) -> None:
    """Raise DatasetError if `size` more bytes do not fit the dataset, the creator's datasets or the disk."""
    if size > quota_left(ds):
        raise DatasetError(f"This would make the dataset larger than {settings.DATASET_MAX_GB} GB, the limit per "
                           "dataset. Use a second dataset for the remaining files.")
    others = Dataset.objects.filter(session_key=ds.session_key, deleted_at__isnull=True).exclude(pk=ds.pk)
    total = sum(used_bytes(d) for d in others) + used_bytes(ds) + size
    if total > settings.DATASET_SESSION_MAX_GB * GB:
        raise DatasetError(f"All datasets started from this browser would hold more than "
                           f"{settings.DATASET_SESSION_MAX_GB} GB. Delete datasets you no longer need.")
    check_disk(size)


def check_disk(size: int) -> None:
    root = settings.DATASET_DIR
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free - size < settings.SCRATCH_MIN_FREE_GB * GB:
        raise DatasetError("The server is short of disk space at the moment. Please try again later.")


# ---------------------------------------------------------------------------
# names

def _split_ext(name: str) -> tuple[str, str]:
    stem = strip_suffixes(name)
    return stem, name[len(stem):]


def unique_file_name(ds: Dataset, name: str) -> str:
    taken = set(ds.files.filter(status=DataFile.READY).values_list("name", flat=True))
    if name not in taken:
        return name
    stem, ext = _split_ext(name)
    n = 2
    while f"{stem}_{n}{ext}" in taken:
        n += 1
    return f"{stem}_{n}{ext}"


def sample_name_from_file(name: str) -> tuple[str, str]:
    """(sample name, lane tag or '') from a file name, e.g. 'CRC_001_S1_L002_R1_001.fastq.gz' -> ('CRC_001', '_L002')."""
    stem = strip_suffixes(name)
    m = ILLUMINA_RE.match(stem)
    base, lane = (m.group("base"), m.group("lane") or "") if m else (stem, "")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._-")[:64] or "sample"
    return base, lane


def unique_sample_name(ds: Dataset, name: str, taken: set[str] | None = None) -> str:
    taken = set(ds.samples.values_list("name", flat=True)) if taken is None else taken
    if name not in taken:
        return name
    n = 2
    while f"{name[:60]}_{n}" in taken:
        n += 1
    return f"{name[:60]}_{n}"


def _add_sample_for(ds: Dataset, df: DataFile) -> Sample:
    base, lane = sample_name_from_file(df.name)
    if lane:
        existing = ds.samples.filter(name=base).first()
        if existing:
            names = DataFile.objects.filter(tag__in=existing.files).values_list("name", flat=True)
            parsed = [sample_name_from_file(n) for n in names]
            if parsed and all(b == base and other_lane and other_lane != lane for b, other_lane in parsed):
                existing.files = existing.files + [df.tag]
                existing.save(update_fields=["files"])
                return existing
    position = (ds.samples.aggregate(m=Max("position"))["m"] or 0) + 1
    return Sample.objects.create(dataset=ds, position=position, name=unique_sample_name(ds, base), files=[df.tag])


def register_file(ds: Dataset, path: Path, name: str, source: str, origin: str = "", file_format: str = "",
                  df: DataFile | None = None) -> DataFile:
    """Move a checked read file into the dataset and give it a sample. `df` is an existing row to fill in."""
    with transaction.atomic():
        Dataset.objects.select_for_update().get(pk=ds.pk)  # one file at a time per dataset: unique names
        if df is None:
            df = DataFile(dataset=ds, source=source, origin=origin[:2000])
        df.name = unique_file_name(ds, os.path.basename(name)[:255] or "reads")
        df.stored_name = f"{df.tag}_{safe_name(df.name)}"
        df.size = path.stat().st_size
        df.file_format = file_format
        df.status = DataFile.READY
        ds.files_dir().mkdir(parents=True, exist_ok=True)
        os.replace(path, df.path())
        df.save()
        _add_sample_for(ds, df)
    ds.touch()
    return df


# ---------------------------------------------------------------------------
# browser uploads

def add_upload(u: Upload) -> DataFile:
    """A complete upload (u.file_format set, 'archive' for archives) goes into its dataset."""
    ds = u.dataset
    if u.file_format == "archive":
        df = DataFile.objects.create(dataset=ds, source=DataFile.UPLOAD, name=u.filename[:255], size=u.size,
                                     status=DataFile.UNPACKING, is_archive=True)
        df.incoming().mkdir(parents=True, exist_ok=True)
        os.replace(u.path(), df.incoming() / safe_name(u.filename))
        _queue("unpack_archive", df)
    else:
        df = register_file(ds, u.path(), u.filename, DataFile.UPLOAD, file_format=u.file_format)
    shutil.rmtree(u.directory(), ignore_errors=True)
    u.status = Upload.USED
    u.save(update_fields=["status", "updated_at"])
    return df


def _queue(task_name: str, df: DataFile) -> None:
    from . import tasks
    getattr(tasks, task_name).apply_async((df.pk,), queue=settings.TRANSFER_QUEUE)


# ---------------------------------------------------------------------------
# links

def _name_from_url(url: str) -> str:
    parts = urlsplit(url)
    name = os.path.basename(unquote(parts.path).rstrip("/"))
    return name[:255] or parts.hostname or "link"


def add_links(ds: Dataset, text: str) -> tuple[list[DataFile], list[str]]:
    """One DataFile and one download task per link; returns (files, error messages)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_LINKS:
        return [], [f"Please add at most {MAX_LINKS} links at a time."]
    files, errors = [], []
    for url in lines:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https", "ftp") or not parts.hostname:
            errors.append(f"{url[:200]}: not an http, https or ftp link.")
            continue
        if len(url) > 2000:
            errors.append(f"{url[:200]}…: the link is too long.")
            continue
        files.append(DataFile.objects.create(dataset=ds, source=DataFile.LINK, origin=url, name=_name_from_url(url),
                                             status=DataFile.TRANSFERRING))
    if files:
        try:
            check_disk(0)
        except DatasetError as exc:
            DataFile.objects.filter(pk__in=[f.pk for f in files]).update(status=DataFile.FAILED, message=str(exc))
            return [], [str(exc)]
        ds.touch()
        for df in files:
            _queue("fetch_link", df)
    return files, errors


def _fail(df: DataFile, message: str) -> None:
    shutil.rmtree(df.incoming(), ignore_errors=True)
    df.status = DataFile.FAILED
    df.message = message[:2000]
    df.save(update_fields=["status", "message", "updated_at"])


def run_fetch_link(file_id: int) -> None:
    from .fetch import BadURL, fetch
    df = DataFile.objects.select_related("dataset").filter(pk=file_id, status=DataFile.TRANSFERRING).first()
    if df is None or df.dataset.deleted_at:
        return
    ds = df.dataset
    incoming = df.incoming()
    shutil.rmtree(incoming, ignore_errors=True)  # a restarted task starts again
    incoming.mkdir(parents=True)
    try:
        max_bytes = quota_left(ds)
        if max_bytes <= 0:
            raise DatasetError(f"The dataset has reached {settings.DATASET_MAX_GB} GB, the limit per dataset.")
        check_disk(0)
        path = fetch(df.origin, incoming, max_bytes, lambda text: log.info("dataset %s: %s", ds.slug, text))
        df.name = path.name[:255]
        df.size = path.stat().st_size
        if is_archive(path):
            df.is_archive = True
            df.status = DataFile.UNPACKING
            df.save(update_fields=["name", "size", "is_archive", "status", "updated_at"])
            run_unpack(df.pk)
            return
        if df.size > settings.UPLOAD_MAX_GB * GB:
            raise DatasetError(f"The file is larger than {settings.UPLOAD_MAX_GB} GB, the limit per read file. "
                               "Compress it with gzip, or put several files into an archive.")
        try:
            fmt = sniff(path)
        except NotReads as exc:
            raise DatasetError(str(exc))
        register_file(ds, path, path.name, DataFile.LINK, file_format=fmt, df=df)
        shutil.rmtree(incoming, ignore_errors=True)
    except (BadURL, DatasetError) as exc:
        _fail(df, str(exc))
    except Exception:
        log.exception("download of %s failed", df.origin)
        _fail(df, "The download failed because of an error on the server.")


def progress_bytes(df: DataFile) -> int:
    """Bytes downloaded so far (links) or written so far (archives)."""
    return _dir_bytes(df.incoming())


# ---------------------------------------------------------------------------
# archives

class _Members:
    """Read files of an archive, as (name, file object opener); skipped members are counted."""

    def __init__(self):
        self.skipped: list[str] = []
        self.count = 0

    def take(self, name: str) -> bool:
        self.count += 1
        if self.count > settings.ARCHIVE_MAX_MEMBERS:
            raise DatasetError(f"The archive holds more than {settings.ARCHIVE_MAX_MEMBERS} files.")
        base = os.path.basename(name.replace("\\", "/"))
        if not base or base.startswith(".") or "__MACOSX/" in name:
            return False  # system files (macOS resource forks, .DS_Store)
        if not allowed_name(base):
            self.skipped.append(base)
            return False
        return True


def _copy_limited(src, dest: Path, limit: int, what: str) -> int:
    done = 0
    with open(dest, "wb") as out:
        while True:
            block = src.read(BLOCK)
            if not block:
                break
            done += len(block)
            if done > limit:
                raise DatasetError(what)
            out.write(block)
            if done % GB < BLOCK:
                check_disk(0)
    return done


def _zip_members(path: Path, members: _Members):
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            kind = stat.S_IFMT(info.external_attr >> 16)  # 0 when the zip tool stored no file type
            if kind and kind != stat.S_IFREG:
                continue  # symbolic links and other special entries
            if not members.take(info.filename):
                continue
            if info.flag_bits & 0x1:
                members.skipped.append(os.path.basename(info.filename) + " (encrypted)")
                continue
            with zf.open(info) as src:
                yield info.filename, src


def _tar_members(path: Path, members: _Members):
    with tarfile.open(path, "r:*") as tf:
        for m in tf:
            if not m.isreg():
                continue  # folders, links, devices
            if not members.take(m.name):
                continue
            src = tf.extractfile(m)
            if src is not None:
                with src:
                    yield m.name, src


def run_unpack(file_id: int) -> None:
    df = DataFile.objects.select_related("dataset").filter(pk=file_id, status=DataFile.UNPACKING).first()
    if df is None or df.dataset.deleted_at:
        return
    ds = df.dataset
    incoming = df.incoming()
    archives = [p for p in incoming.iterdir() if p.is_file()] if incoming.is_dir() else []
    if len(archives) != 1:
        _fail(df, "archive: the archive file is missing; please add it again.")
        return
    archive = archives[0]
    work = incoming / "members"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    members = _Members()
    added = 0
    try:
        budget = quota_left(ds) + archive.stat().st_size + _dir_bytes(work)
        reader = _zip_members if zipfile.is_zipfile(archive) else _tar_members
        for i, (name, src) in enumerate(reader(archive, members)):
            base = os.path.basename(name.replace("\\", "/"))
            limit = min(settings.UPLOAD_MAX_GB * GB, budget)
            tmp = work / f"{i}"
            done = _copy_limited(src, tmp, limit,
                                 f"{base} is larger than {settings.UPLOAD_MAX_GB} GB, the limit per read file"
                                 if limit < budget else
                                 f"The unpacked files exceed {settings.DATASET_MAX_GB} GB, the limit per dataset.")
            try:
                fmt = sniff(tmp)
            except NotReads as exc:
                tmp.unlink()
                members.skipped.append(f"{base} ({str(exc).rstrip('.')})")
                continue
            register_file(ds, tmp, base, DataFile.ARCHIVE, origin=df.name, file_format=fmt)
            budget -= done
            added += 1
    except (DatasetError, zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, NotImplementedError) as exc:
        what = str(exc) if isinstance(exc, DatasetError) else f"the archive cannot be read ({exc})"
        _fail(df, f"archive: {what}" + (f"; {added} read files were added before" if added else ""))
        return
    except Exception:
        log.exception("unpacking %s failed", df.name)
        _fail(df, "archive: unpacking failed because of an error on the server.")
        return
    shutil.rmtree(incoming, ignore_errors=True)
    message = f"{added} read file{'s' if added != 1 else ''} added"
    if members.skipped:
        shown = ", ".join(members.skipped[:SKIPPED_SHOWN])
        more = f" and {len(members.skipped) - SKIPPED_SHOWN} more" if len(members.skipped) > SKIPPED_SHOWN else ""
        message += f"; skipped: {shown}{more}"
    df.status = DataFile.UNPACKED if added else DataFile.FAILED
    df.message = (message if added else f"archive: no read files found; {message}")[:2000]
    df.save(update_fields=["status", "message", "updated_at"])
    ds.touch()


# ---------------------------------------------------------------------------
# samples and annotations

def validate_sample_name(name: str) -> str:
    name = name.strip()
    if not re.match(Sample.NAME_RE, name):
        raise DatasetError("Names may have up to 64 letters, digits, '.', '_' and '-', and start with a letter "
                           "or digit.")
    return name


def validate_column(name: str, existing: list[str]) -> str:
    name = name.strip()
    if not name or len(name) > 64 or any(c in name for c in "\t\r\n"):
        raise DatasetError("Column names need 1 to 64 characters.")
    if name.lower() in ("file", "name") or name in existing:
        raise DatasetError(f"There is already a column “{name}”.")
    if len(existing) >= 50:
        raise DatasetError("A dataset can have at most 50 annotation columns.")
    return name


def clean_value(value) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())[:200]


def edit_cells(ds: Dataset, edits: list[dict]) -> dict[str, str]:
    """Apply [{sample, field: name|column, column, value}]; returns {"<sample>:<field>[:<column>]": error}."""
    errors = {}
    samples = {s.pk: s for s in ds.samples.all()}
    names = {s.name for s in samples.values()}
    for e in edits[:20000]:
        try:
            s = samples.get(int(e.get("sample")))
        except (TypeError, ValueError):
            s = None
        field = e.get("field")
        key = f"{e.get('sample')}:{field}" + (f":{e.get('column')}" if field == "column" else "")
        if s is None:
            errors[key] = "This sample does not exist any more; reload the page."
            continue
        try:
            if field == "name":
                new = validate_sample_name(str(e.get("value", "")))
                if new != s.name:
                    if new in names:
                        raise DatasetError(f"There is already a sample “{new}”.")
                    names.discard(s.name)
                    names.add(new)
                    s.name = new
                    s.save(update_fields=["name"])
            elif field == "column" and e.get("column") in ds.columns:
                s.annotations = {**s.annotations, e["column"]: clean_value(e.get("value"))}
                s.save(update_fields=["annotations"])
            else:
                raise DatasetError("Unknown column; reload the page.")
        except DatasetError as exc:
            errors[key] = str(exc)
    return errors


def add_column(ds: Dataset, name: str) -> None:
    ds.columns = ds.columns + [validate_column(name, ds.columns)]
    ds.save(update_fields=["columns"])


def remove_column(ds: Dataset, name: str) -> None:
    if name not in ds.columns:
        return
    ds.columns = [c for c in ds.columns if c != name]
    ds.save(update_fields=["columns"])
    for s in ds.samples.all():
        if name in s.annotations:
            s.annotations = {k: v for k, v in s.annotations.items() if k != name}
            s.save(update_fields=["annotations"])


def remove_samples(ds: Dataset, ids) -> int:
    """Delete samples and their files."""
    if active_jobs(ds).exists():
        raise DatasetError("A job is using this dataset; remove samples when it has finished.")
    samples = list(ds.samples.filter(pk__in=ids))
    tags = [t for s in samples for t in s.files]
    for df in ds.files.filter(tag__in=tags):
        df.path().unlink(missing_ok=True)
        df.delete()
    for s in samples:
        s.delete()
    return len(samples)


def sample_rows(ds: Dataset) -> list[dict]:
    """Samples with their files, for pages and sheets."""
    files = {f.tag: f for f in ds.files.filter(status=DataFile.READY)}
    rows = []
    for s in ds.samples.all():
        fs = [files[t] for t in s.files if t in files]
        rows.append({"sample": s, "files": fs, "size": sum(f.size for f in fs),
                     "values": [s.annotations.get(c, "") for c in ds.columns]})
    return rows
