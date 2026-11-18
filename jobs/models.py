"""Jobs of all services (miSRA, sRNAtoolbox): one row per run of a tool, results in JOB_DIR/<slug>/."""
import secrets
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


def new_slug():
    return secrets.token_urlsafe(9)  # 12 characters, not guessable: the job link is the only access control


class Job(models.Model):
    QUEUED, RUNNING, DONE, FAILED, EXPIRED = "queued", "running", "done", "failed", "expired"
    STATUSES = [(QUEUED, "waiting"), (RUNNING, "running"), (DONE, "finished"), (FAILED, "failed"),
                (EXPIRED, "results deleted")]

    slug = models.CharField(max_length=20, unique=True, default=new_slug)
    app = models.CharField(max_length=20, db_index=True, help_text="URL namespace of the service: misra, srnatoolbox")
    tool = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=STATUSES, default=QUEUED, db_index=True)
    params = models.JSONField(default=dict, blank=True, help_text="Options of the tool, shown on the job page")
    inputs = models.JSONField(default=dict, blank=True,
                              help_text="What the job runs on; defined by the service (e.g. miSRA: exp_ids, source)")
    n_items = models.PositiveIntegerField(default=0, help_text="Experiments or samples")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children",
                               help_text="Job whose results this job uses")
    results = models.JSONField(default=list, blank=True, help_text="[{name, label, size}] files in the job directory")
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.app}/{self.tool} {self.slug} ({self.status})"

    def directory(self) -> Path:
        return Path(settings.JOB_DIR) / self.slug

    @property
    def is_active(self) -> bool:
        return self.status in (self.QUEUED, self.RUNNING)

    @property
    def tool_info(self):
        from .registry import get_tool
        return get_tool(self.app, self.tool)

    def get_absolute_url(self):
        return reverse(f"{self.app}:job", args=[self.slug])


def new_token():
    return secrets.token_urlsafe(18)


class Upload(models.Model):
    """A file sent from the browser in chunks (jobs/uploads.py), going into a dataset or waiting for a job.

    The file lives in UPLOAD_DIR/<token>/<stored_name>; when a job uses it, it is moved into the job directory.
    Only the browser session that started the upload can continue or use it.
    """

    UPLOADING, COMPLETE, USED, FAILED = "uploading", "complete", "used", "failed"
    STATUSES = [(UPLOADING, "uploading"), (COMPLETE, "complete"), (USED, "used by a job"), (FAILED, "failed")]

    token = models.CharField(max_length=40, unique=True, default=new_token)
    session_key = models.CharField(max_length=64, db_index=True)
    filename = models.CharField(max_length=255, help_text="Name of the file on the user's computer")
    stored_name = models.CharField(max_length=255)
    size = models.BigIntegerField(help_text="Announced size in bytes")
    received = models.BigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUSES, default=UPLOADING, db_index=True)
    file_format = models.CharField(max_length=20, blank=True, help_text="fastq, fasta (+ .gz/.bz2), set on finish")
    message = models.CharField(max_length=500, blank=True)
    job = models.ForeignKey(Job, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    dataset = models.ForeignKey("Dataset", null=True, blank=True, on_delete=models.CASCADE, related_name="uploads",
                                help_text="Dataset the file goes into when complete")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"upload {self.token} {self.filename} ({self.status})"

    def directory(self) -> Path:
        return Path(settings.UPLOAD_DIR) / self.token

    def path(self) -> Path:
        return self.directory() / self.stored_name


class Dataset(models.Model):
    """Read files uploaded or linked once and used by several jobs (jobs/datasets.py).

    Files live in DATASET_DIR/<slug>/files/. The link is the only access control, like the job link.
    The dataset is deleted DATASET_MAX_AGE_DAYS after its last use (file added, job started or finished),
    never while a job using it is waiting or running.
    """

    slug = models.CharField(max_length=20, unique=True, default=new_slug)
    app = models.CharField(max_length=20, help_text="URL namespace of the service that shows the dataset")
    title = models.CharField(max_length=200)
    session_key = models.CharField(max_length=64, db_index=True, help_text="Browser session that created it")
    columns = models.JSONField(default=list, blank=True, help_text="Annotation column names, in order")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(default=timezone.now, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"dataset {self.slug} {self.title}"

    def directory(self) -> Path:
        return Path(settings.DATASET_DIR) / self.slug

    def files_dir(self) -> Path:
        return self.directory() / "files"

    def incoming_dir(self) -> Path:
        return self.directory() / "incoming"

    @property
    def expires_at(self):
        return self.last_used_at + timedelta(days=settings.DATASET_MAX_AGE_DAYS)

    def touch(self) -> None:
        self.last_used_at = timezone.now()
        Dataset.objects.filter(pk=self.pk).update(last_used_at=self.last_used_at)

    def get_absolute_url(self):
        return reverse(f"{self.app}:dataset", args=[self.slug])


def new_tag():
    return secrets.token_hex(5)  # 10 characters, used in file names on disk


class DataFile(models.Model):
    """One file of a dataset: a read file ready for use, or an upload/link/archive still being processed."""

    TRANSFERRING, UNPACKING, READY, UNPACKED, SKIPPED, FAILED = \
        "transferring", "unpacking", "ready", "unpacked", "skipped", "failed"
    STATUSES = [(TRANSFERRING, "downloading"), (UNPACKING, "unpacking"), (READY, "ready"),
                (UNPACKED, "archive unpacked"), (SKIPPED, "skipped"), (FAILED, "failed")]
    UPLOAD, LINK, ARCHIVE = "upload", "link", "archive"
    SOURCES = [(UPLOAD, "uploaded"), (LINK, "link"), (ARCHIVE, "from an archive")]

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="files")
    tag = models.CharField(max_length=12, unique=True, default=new_tag)
    name = models.CharField(max_length=255, help_text="File name shown to the user and used in sample sheets")
    stored_name = models.CharField(max_length=255, blank=True)
    size = models.BigIntegerField(default=0)
    file_format = models.CharField(max_length=20, blank=True)
    source = models.CharField(max_length=10, choices=SOURCES)
    origin = models.CharField(max_length=2000, blank=True, help_text="Link, or name of the archive")
    status = models.CharField(max_length=12, choices=STATUSES, db_index=True)
    is_archive = models.BooleanField(default=False, help_text="An archive; its read files become DataFiles")
    message = models.CharField(max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.tag} {self.name} ({self.status})"

    def path(self) -> Path:
        return self.dataset.files_dir() / self.stored_name

    def incoming(self) -> Path:
        """Working directory of a link download or archive."""
        return self.dataset.incoming_dir() / self.tag


class Sample(models.Model):
    """A sample of a dataset: one or more files (lanes, merged by sRNAbench) and its annotation values."""

    NAME_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="samples")
    position = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=64)
    files = models.JSONField(default=list, help_text="Tags of the DataFiles, in order")
    annotations = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["dataset", "name"], name="unique_sample_name")]

    def __str__(self):
        return f"sample {self.name} of {self.dataset_id}"
