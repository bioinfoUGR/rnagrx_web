import logging
import traceback

from celery import shared_task
from django.utils import timezone

from .models import Job
from .registry import get_tool

log = logging.getLogger(__name__)


@shared_task(acks_late=True)
def run_job(job_id: int) -> None:
    job = Job.objects.filter(pk=job_id, status=Job.QUEUED).first()
    if job is None:  # already started elsewhere, or deleted
        return
    job.status = Job.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    directory = job.directory()
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / "job.log"

    def write_log(text: str) -> None:
        with open(logfile, "a") as fh:
            fh.write(f"{timezone.localtime():%H:%M:%S}  {text}\n")

    try:
        tool = get_tool(job.app, job.tool)
        if tool is None:
            raise RuntimeError(f"unknown tool {job.app}/{job.tool}")
        results = tool.run(job, directory, write_log)
        for r in results:
            r["size"] = (directory / r["name"]).stat().st_size
        job.results = results
        job.status = Job.DONE
        write_log("finished")
    except Exception as exc:
        log.exception("job %s failed", job.slug)
        write_log("FAILED: " + "".join(traceback.format_exception_only(exc)).strip())
        job.status = Job.FAILED
        job.message = str(exc)[:4000]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "results", "message", "finished_at"])


def submit(job: Job) -> None:
    """Queue a job on the queue of its tool."""
    run_job.apply_async((job.pk,), queue=job.tool_info.queue)


# dataset transfers (queue TRANSFER_QUEUE, see jobs/datasets.py)

@shared_task(acks_late=True)
def fetch_link(file_id: int) -> None:
    from .datasets import run_fetch_link
    run_fetch_link(file_id)


@shared_task(acks_late=True)
def unpack_archive(file_id: int) -> None:
    from .datasets import run_unpack
    run_unpack(file_id)
