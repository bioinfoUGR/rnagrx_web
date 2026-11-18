"""Jobs of all services (miSRA, sRNAtoolbox): one row per run of a tool, results in JOB_DIR/<slug>/."""
import secrets
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse


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
    """A file sent from the browser in chunks (jobs/uploads.py), waiting to be used by a job.

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
