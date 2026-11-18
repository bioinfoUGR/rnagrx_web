"""Job page and result files, shared by the services.

Each service routes these views in its own URL namespace and passes `app`, e.g.
    path("job/<str:slug>/", jobs.views.job_view, {"app": "misra"}, name="job")
and provides `<app>/job.html` (extends the service's base, includes jobs/_job_status.html) and
`<app>/_job_inputs.html` (table rows describing Job.inputs).
"""
from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET

from .models import Job

LOG_LINES = 40
SESSION_JOBS_MAX = 20


def _session_key(app: str) -> str:
    return f"{app}_jobs"


def remember_job(request, job: Job) -> None:
    """Keep the job in the session's list of recent jobs of its service."""
    key = _session_key(job.app)
    slugs = [s for s in request.session.get(key, []) if s != job.slug]
    request.session[key] = [job.slug] + slugs[:SESSION_JOBS_MAX - 1]


def recent_jobs(request, app: str) -> list[Job]:
    slugs = request.session.get(_session_key(app), [])
    return list(Job.objects.filter(app=app, slug__in=slugs).order_by("-pk"))


def log_tail(job: Job) -> str:
    path = job.directory() / "job.log"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-LOG_LINES:])


def status_context(job: Job) -> dict:
    return {"job": job, "tool": job.tool_info, "log": log_tail(job), "file_view": f"{job.app}:job_file",
            "inputs_template": f"{job.app}/_job_inputs.html", "max_age_days": settings.JOB_MAX_AGE_DAYS}


@require_GET
def job_view(request, slug, app):
    job = get_object_or_404(Job, slug=slug, app=app)
    remember_job(request, job)
    context = status_context(job)
    if request.htmx and not request.htmx.history_restore_request:
        response = render(request, "jobs/_job_status.html", context)
    else:
        response = render(request, f"{app}/job.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response


@require_GET
def job_file(request, slug, name, app):
    job = get_object_or_404(Job, slug=slug, app=app)
    if job.status != Job.DONE or name not in {r["name"] for r in job.results}:
        raise Http404
    path = job.directory() / name
    if not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)
