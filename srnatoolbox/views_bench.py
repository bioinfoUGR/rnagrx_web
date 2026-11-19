"""sRNAbench: settings page for a selection of dataset samples, species search, job and sample pages."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods

from jobs.models import DataFile, Dataset, Job
from jobs.tasks import submit
from jobs.views import job_view as generic_job_view
from jobs.views import log_tail, remember_job, status_context

from . import species as species_index
from .forms import COPYABLE, MIRNA_LABELS, PROTOCOL_LABELS, SrnabenchForm
from .runners import BENCH_DIR, input_file
from .summary import load_summary, progress, sample_report
from .tools import TOOLS
from .views_data import SESSION_DATASETS


def _selection(ds: Dataset, ids: list[int]):
    """(samples, problems) of a selection; a sample is usable when all its files are ready."""
    ready = set(ds.files.filter(status=DataFile.READY).values_list("tag", flat=True))
    samples = list(ds.samples.filter(pk__in=ids))
    problems = [f"{s.name}: its files are not complete yet." for s in samples if not set(s.files) <= ready]
    return samples, problems


def _copied_settings(request) -> dict:
    """Initial values from an earlier sRNAbench job (?from=<slug>)."""
    slug = request.GET.get("from", "")
    job = Job.objects.filter(slug=slug, app="srnatoolbox", tool="srnabench").first() if slug else None
    if job is None:
        return {}
    initial = {k: v for k, v in job.params.items() if k in COPYABLE and v is not None}
    initial["from_job"] = job.slug
    return initial


@require_http_methods(["GET", "POST"])
def srnabench_view(request):
    tool = TOOLS["srnabench"]
    src = request.POST if request.method == "POST" else request.GET
    ds = Dataset.objects.filter(slug=src.get("dataset", ""), app="srnatoolbox", deleted_at__isnull=True).first()
    if ds is None:
        slugs = request.session.get(SESSION_DATASETS, [])
        datasets = Dataset.objects.filter(slug__in=slugs, deleted_at__isnull=True)
        return render(request, "srnatoolbox/srnabench_start.html", {"tool": tool, "datasets": datasets})
    ids = [int(x) for x in src.getlist("s") if x.isdigit()]
    samples, problems = _selection(ds, ids)
    if not samples:
        problems.append("No samples are selected.")
    if len(samples) > settings.SRNABENCH_MAX_SAMPLES:
        problems.append(f"{len(samples)} samples are selected; one job can profile up to "
                        f"{settings.SRNABENCH_MAX_SAMPLES}. Select fewer and start a second job for the rest.")
    previous = Job.objects.filter(app="srnatoolbox", tool="srnabench", inputs__dataset=ds.slug).order_by("-id")[:20]

    if request.method == "POST":
        form = SrnabenchForm(request.POST, request.FILES)
        if form.is_valid() and not problems:
            job = _create_job(form, ds, samples)
            remember_job(request, job)
            return redirect(job)
    else:
        form = SrnabenchForm(initial=_copied_settings(request))
    key = form["species"].value() or ""
    groups = {}
    if ds.columns:
        for s in samples:
            v = s.annotations.get(ds.columns[0], "")
            groups[v] = groups.get(v, 0) + 1
    n_genome, n_mirna_only = species_index.counts()
    return render(request, "srnatoolbox/srnabench_form.html", {
        "tool": tool, "form": form, "ds": ds, "samples": samples, "problems": problems,
        "size": sum(f.size for f in ds.files.filter(tag__in=[t for s in samples for t in s.files])),
        "groups": sorted(groups.items()), "group_column": ds.columns[0] if ds.columns else "",
        "species": species_index.get(key), "previous": previous, "copied": form.initial.get("from_job"),
        "n_genome": n_genome, "n_mirna_only": n_mirna_only, "max_samples": settings.SRNABENCH_MAX_SAMPLES,
        "max_age_days": settings.JOB_MAX_AGE_DAYS, "mirna_labels": MIRNA_LABELS})


def _create_job(form, ds: Dataset, samples) -> Job:
    params = form.params()
    job = Job.objects.create(
        app="srnatoolbox", tool="srnabench", params=params, n_items=len(samples),
        inputs={"dataset": ds.slug, "dataset_title": ds.title, "columns": ds.columns,
                "samples": [{"name": s.name, "files": s.files, "annotations": s.annotations} for s in samples]})
    for key in ("mature", "hairpin", "libs", "spikes"):
        fasta = form.cleaned_data.get(key)
        if fasta and (key in ("libs", "spikes") or params["mirdb"] == "own"):
            path = input_file(job, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(fasta.text())
    ds.touch()
    submit(job)
    return job


@require_GET
def species_search(request):
    hits = species_index.search(request.GET.get("q", ""))
    return render(request, "srnatoolbox/_species_hits.html", {"hits": hits, "mirna_labels": MIRNA_LABELS})


# ---------------------------------------------------------------------------
# job and sample pages

def settings_rows(job: Job) -> list[tuple[str, str]]:
    p = job.params
    ref = MIRNA_LABELS.get(p.get("mirdb"), p.get("mirdb"))
    if p.get("mirdb") == "own":
        ref = f"own: {p.get('mature_file', '')}, {p.get('hairpin_file', '')}"
    protocol = PROTOCOL_LABELS.get(p.get("protocol"), p.get("protocol"))
    if p.get("protocol") == "custom":
        protocol = f"adapter {p.get('adapter')}, random nt 5' {p.get('random_5p')} / 3' {p.get('random_3p')}"
    rows = [("Species", f"{p.get('species_name')} · " + (f"genome {p['accession']}" if p.get("accession")
                                                          else f"taxon {p.get('taxon')}, microRNAs only")),
            ("microRNA reference", ref),
            ("Protocol", protocol),
            ("Read length", f"{p.get('min_len')}–{p.get('max_len') or 'no limit'} nt"),
            ("Min. read count", p.get("min_rc")), ("Mismatches", p.get("mm")),
            ("Adapter", f"{p.get('adapter_mm')} mismatches, at least {p.get('min_adapter_length')} nt"),
            ("Low-complexity filter", "on" if p.get("low_complexity", True) else "off"),
            ("Seed expression", "yes" if p.get("seed_expression") else "no")]
    for key, label in (("libs_file", "Additional references"), ("spikes_file", "Spike-ins")):
        if p.get(key):
            rows.append((label, p[key]))
    return rows


def _rerun_url(job: Job) -> str:
    """Settings page for the same samples (as far as they still exist) and settings."""
    ds = Dataset.objects.filter(slug=job.inputs.get("dataset", ""), deleted_at__isnull=True).first()
    if ds is None:
        return ""
    names = [s["name"] for s in job.inputs.get("samples", [])]
    ids = ds.samples.filter(name__in=names).values_list("pk", flat=True)
    from urllib.parse import urlencode
    from django.urls import reverse
    return reverse("srnatoolbox:tool", args=["srnabench"]) + "?" + urlencode(
        {"dataset": ds.slug, "s": list(ids), "from": job.slug}, doseq=True)


def job_view(request, slug):
    job = get_object_or_404(Job, slug=slug, app="srnatoolbox")
    if job.tool == "srnade":
        from .views_de import srnade_job_view
        return srnade_job_view(request, job)
    if job.tool != "srnabench":
        return generic_job_view(request, slug, app="srnatoolbox")
    remember_job(request, job)
    context = {**status_context(job), "settings_rows": settings_rows(job), "rerun_url": _rerun_url(job),
               "dataset": Dataset.objects.filter(slug=job.inputs.get("dataset", "")).first(),
               "de_jobs": job.children.filter(tool="srnade").order_by("-id")}
    if job.is_active:
        context["progress"] = progress(job)
    if job.status == Job.DONE:
        context["summary"] = load_summary(job)
    if request.htmx and not request.htmx.history_restore_request:
        response = render(request, "srnatoolbox/_srnabench_status.html", context)
    else:
        response = render(request, "srnatoolbox/srnabench_job.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response


@require_GET
def sample_view(request, slug, name):
    job = get_object_or_404(Job, slug=slug, app="srnatoolbox", tool="srnabench", status=Job.DONE)
    names = [s["name"] for s in job.inputs.get("samples", [])]
    if name not in names:
        raise Http404
    i = names.index(name)
    report = sample_report(job, name)
    if report is None:
        raise Http404
    return render(request, "srnatoolbox/srnabench_sample.html", {
        "job": job, "name": name, "report": report, "sample": job.inputs["samples"][i],
        "columns": job.inputs.get("columns", []), "prev": names[i - 1] if i > 0 else "",
        "next": names[i + 1] if i + 1 < len(names) else "", "max_age_days": settings.JOB_MAX_AGE_DAYS,
        "log": log_tail(job)})


def _finished_sample(slug: str, name: str):
    job = get_object_or_404(Job, slug=slug, app="srnatoolbox", tool="srnabench", status=Job.DONE)
    if name not in [s["name"] for s in job.inputs.get("samples", [])]:
        raise Http404
    sample_dir = job.directory() / BENCH_DIR / name
    if not sample_dir.is_dir():
        raise Http404
    return job, sample_dir


@require_GET
def sample_file(request, slug, name, path):
    job, sample_dir = _finished_sample(slug, name)
    target = (sample_dir / path).resolve()
    if not target.is_relative_to(sample_dir.resolve()) or not target.is_file():
        raise Http404
    return FileResponse(open(target, "rb"), as_attachment=True, filename=target.name)


@require_GET
def sample_zip(request, slug, name):
    """The sample's sRNAbench output as ZIP; made on the first request and kept with the job."""
    job, sample_dir = _finished_sample(slug, name)
    zips = job.directory() / "sample_zips"
    target = zips / f"{name}.zip"
    if not target.is_file():
        zips.mkdir(exist_ok=True)
        tmp = zips / f".{name}.{os.getpid()}.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in sorted(sample_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, Path(name) / p.relative_to(sample_dir))
        os.replace(tmp, target)
    return FileResponse(open(target, "rb"), as_attachment=True, filename=f"{name}_srnabench.zip")
