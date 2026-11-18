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
