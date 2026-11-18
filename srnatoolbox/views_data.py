"""Datasets ("My data"): read files uploaded or linked once, samples and their annotation, used by the tools.

The dataset link is the only access control (like job links). The logic lives in jobs/datasets.py and
jobs/samplesheet.py; these views render the sRNAtoolbox pages and HTMX fragments.
"""
import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from jobs.datasets import (DatasetError, active_jobs, add_column, add_links, create_dataset, delete_dataset,
                           edit_cells, progress_bytes, ready_bytes, remove_column, remove_samples, sample_rows)
from jobs.models import DataFile, Dataset, Job, Upload
from jobs.samplesheet import FORMATS, SheetError, apply, drop_pending, export, load_pending, parse, plan, \
    save_pending

APP = "srnatoolbox"
SESSION_DATASETS = "srnatoolbox_datasets"
SESSION_DATASETS_MAX = 50
STALE_UPLOAD_SECONDS = 120


def remember_dataset(request, ds: Dataset) -> None:
    slugs = [s for s in request.session.get(SESSION_DATASETS, []) if s != ds.slug]
    request.session[SESSION_DATASETS] = [ds.slug] + slugs[:SESSION_DATASETS_MAX - 1]


def _dataset(slug: str) -> Dataset:
    ds = Dataset.objects.filter(slug=slug, app=APP).first()
    if ds is None:
        raise Http404
    return ds


def _live(slug: str) -> Dataset:
    ds = _dataset(slug)
    if ds.deleted_at:
        raise Http404
    return ds


def _limits() -> dict:
    return {"max_gb": settings.DATASET_MAX_GB, "file_gb": settings.UPLOAD_MAX_GB,
            "max_age_days": settings.DATASET_MAX_AGE_DAYS}


# ---------------------------------------------------------------------------
# pages

@require_GET
def data_home(request):
    slugs = request.session.get(SESSION_DATASETS, [])
    found = {d.slug: d for d in Dataset.objects.filter(slug__in=slugs, app=APP, deleted_at__isnull=True)}
    datasets = []
    for slug in slugs:
        if slug in found:
            d = found[slug]
            datasets.append({"ds": d, "n_samples": d.samples.count(), "size": ready_bytes(d)})
    return render(request, "srnatoolbox/data_home.html", {"datasets": datasets, **_limits()})


@require_POST
def data_new(request):
    if not request.session.session_key:
        request.session.save()
    ds = create_dataset(APP, request.session.session_key, request.POST.get("title", ""))
    remember_dataset(request, ds)
    return redirect(ds)


@require_GET
def dataset_view(request, slug):
    ds = _dataset(slug)
    if ds.deleted_at:
        return render(request, "srnatoolbox/dataset_gone.html", {"ds": ds, **_limits()}, status=410)
    remember_dataset(request, ds)
    jobs = Job.objects.filter(inputs__dataset=ds.slug).order_by("-id")[:50]
    return render(request, "srnatoolbox/dataset.html", {
        "ds": ds, "jobs": jobs, "busy": active_jobs(ds).exists(), **_samples_context(ds),
        **_transfers_context(request, ds), **_limits()})


# ---------------------------------------------------------------------------
# transfers (links, archives, uploads from other browsers)

def _transfers_context(request, ds: Dataset) -> dict:
    active = []
    for df in ds.files.filter(status__in=[DataFile.TRANSFERRING, DataFile.UNPACKING]):
        active.append({"df": df, "done": progress_bytes(df)})
    stale_before = timezone.now() - timedelta(seconds=STALE_UPLOAD_SECONDS)
    uploads = []
    for u in ds.uploads.filter(status=Upload.UPLOADING):
        mine = u.session_key == request.session.session_key
        if not mine or u.updated_at < stale_before:
            uploads.append({"u": u, "mine": mine, "stale": u.updated_at < stale_before})
    finished = ds.files.filter(status__in=[DataFile.UNPACKED, DataFile.FAILED]).order_by("-updated_at")[:20]
    return {"active": active, "other_uploads": uploads, "finished": finished,
            "polling": bool(active) or any(not x["stale"] for x in uploads),
            "n_ready": ds.files.filter(status=DataFile.READY).count()}


@require_GET
def dataset_transfers(request, slug):
    ds = _live(slug)
    ctx = _transfers_context(request, ds)
    response = render(request, "srnatoolbox/_dataset_transfers.html", {"ds": ds, **ctx})
    if request.GET.get("ready") not in (None, str(ctx["n_ready"])):
        response["HX-Trigger"] = "samples-changed"
    return response


@require_POST
def dataset_links(request, slug):
    ds = _live(slug)
    files, errors = add_links(ds, request.POST.get("links", ""))
    ctx = _transfers_context(request, ds)
    return render(request, "srnatoolbox/_dataset_transfers.html", {"ds": ds, "link_errors": errors,
                                                                  "links_added": len(files), **ctx})


@require_POST
def dataset_transfers_clear(request, slug):
    ds = _live(slug)
    ds.files.filter(status__in=[DataFile.UNPACKED, DataFile.FAILED]).delete()
    return render(request, "srnatoolbox/_dataset_transfers.html", {"ds": ds, **_transfers_context(request, ds)})


# ---------------------------------------------------------------------------
# samples

def _samples_context(ds: Dataset) -> dict:
    rows = sample_rows(ds)
    return {"rows": rows, "total_size": sum(r["size"] for r in rows), "pending": load_pending(ds)}


def _samples(request, ds: Dataset, **extra):
    return render(request, "srnatoolbox/_dataset_samples.html",
                  {"ds": ds, "busy": active_jobs(ds).exists(), **_samples_context(ds), **extra})


@require_GET
def dataset_samples(request, slug):
    return _samples(request, _live(slug))


@require_POST
def dataset_cells(request, slug):
    ds = _live(slug)
    try:
        edits = json.loads(request.body or b"{}").get("edits", [])
        if not isinstance(edits, list):
            raise ValueError
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Bad request."}, status=400)
    errors = edit_cells(ds, [e for e in edits if isinstance(e, dict)])
    return JsonResponse({"errors": errors, "saved": len(edits) - len(errors)})


@require_POST
def dataset_columns(request, slug):
    ds = _live(slug)
    try:
        if request.POST.get("remove"):
            remove_column(ds, request.POST["remove"])
        else:
            add_column(ds, request.POST.get("name", ""))
    except DatasetError as exc:
        return _samples(request, ds, column_error=str(exc))
    return _samples(request, ds)


@require_POST
def dataset_remove(request, slug):
    ds = _live(slug)
    ids = [int(x) for x in request.POST.getlist("s") if x.isdigit()]
    try:
        n = remove_samples(ds, ids)
    except DatasetError as exc:
        return _samples(request, ds, samples_error=str(exc))
    return _samples(request, ds, samples_note=f"{n} sample{'s' if n != 1 else ''} and their files removed.")


# ---------------------------------------------------------------------------
# sample sheet

@require_GET
def dataset_sheet(request, slug, fmt):
    ds = _live(slug)
    if fmt not in FORMATS:
        raise Http404
    response = HttpResponse(export(ds, fmt), content_type=FORMATS[fmt])
    response["Content-Disposition"] = f'attachment; filename="samples_{ds.slug}.{fmt}"'
    return response


@require_POST
def dataset_sheet_upload(request, slug):
    ds = _live(slug)
    f = request.FILES.get("sheet")
    if f is None:
        return _samples(request, ds, sheet_error="Please choose a sample sheet file.")
    try:
        p = plan(ds, parse(f.read(5 * 1024 * 1024 + 1), f.name))
    except SheetError as exc:
        return _samples(request, ds, sheet_error=str(exc))
    p["filename"] = f.name[:200]
    save_pending(ds, p)
    return _samples(request, ds)


@require_POST
def dataset_sheet_apply(request, slug):
    ds = _live(slug)
    p = load_pending(ds)
    if p is None:
        return _samples(request, ds)
    if request.POST.get("cancel"):
        drop_pending(ds)
        return _samples(request, ds)
    if active_jobs(ds).exists():
        return _samples(request, ds, sheet_error="A job is using this dataset; apply the sheet when it has finished.")
    try:
        n = apply(ds, p)
    except SheetError as exc:
        drop_pending(ds)
        return _samples(request, ds, sheet_error=str(exc))
    return _samples(request, ds, samples_note=f"Sample sheet {p.get('filename', '')} applied: {n} samples.")


# ---------------------------------------------------------------------------
# dataset

@require_POST
def dataset_rename(request, slug):
    ds = _live(slug)
    title = request.POST.get("title", "").strip()[:200]
    if title:
        ds.title = title
        ds.save(update_fields=["title"])
    return redirect(ds)


@require_POST
def dataset_delete(request, slug):
    ds = _live(slug)
    if active_jobs(ds).exists():
        messages.error(request, "A job is using this dataset; delete it when the job has finished.")
        return redirect(ds)
    delete_dataset(ds)
    messages.success(request, f"Dataset “{ds.title}” and its files were deleted.")
    return redirect(reverse("srnatoolbox:data"))
