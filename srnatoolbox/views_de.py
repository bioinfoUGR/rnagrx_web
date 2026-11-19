"""sRNAde: form on a finished sRNAbench job (samples, groups, settings), PCA data, job page and files."""
from __future__ import annotations

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods

from jobs.models import Job
from jobs.tasks import submit
from jobs.views import recent_jobs, remember_job, status_context

from .de import DE_DIR, Parent, far_from_group, load_de_summary, pca
from .forms import COUNT_LABELS, DE_METHOD_LABELS, FILTER_LABELS, LIBRARY_LABELS, SrnadeForm
from .tools import TOOLS


def _bench_job(slug: str) -> Job:
    return get_object_or_404(Job, slug=slug, app="srnatoolbox", tool="srnabench")


@require_GET
def srnade_start(request):
    """sRNAde without a job: list this browser's finished sRNAbench jobs."""
    jobs = [j for j in recent_jobs(request, "srnatoolbox") if j.tool == "srnabench" and j.status == Job.DONE]
    return render(request, "srnatoolbox/srnade_start.html", {"tool": TOOLS["srnade"], "jobs": jobs})


@require_http_methods(["GET", "POST"])
def srnade_view(request, slug):
    bench = _bench_job(slug)
    tool = TOOLS["srnade"]
    if bench.status != Job.DONE:
        return render(request, "srnatoolbox/srnade_unavailable.html", {"tool": tool, "bench": bench}, status=409)
    parent = Parent(bench)
    if not parent.usable:
        return render(request, "srnatoolbox/srnade_unavailable.html",
                      {"tool": tool, "bench": bench, "parent": parent}, status=409)
    if request.method == "POST":
        form = SrnadeForm(request.POST, parent=parent)
        if form.is_valid():
            job = _create_job(form, bench)
            remember_job(request, job)
            return redirect(job)
    else:
        initial = _copied_settings(request, bench)
        column = request.GET.get("column") or initial.get("column", "")
        form = SrnadeForm(initial=initial, parent=parent, column=column)
        groups = [g for g, _ in parent.groups(form.column)]
        chosen = initial.get("samples") or [s["name"] for s in parent.samples]
        form.initial.setdefault("groups", groups)
        form.initial["samples"] = chosen
    column = form.column
    far = far_from_group(bench, parent, column)
    if form.is_bound:
        chosen = set(form.data.getlist("samples"))
        groups_on = set(form.data.getlist("groups"))
    else:
        chosen = set(form.initial["samples"])
        groups_on = set(form.initial["groups"])
    rows = []
    for s in parent.samples:
        g = s["annotations"].get(column, "")
        flags = s["flags"] + (["far from its group (PCA)"] if s["name"] in far else [])
        rows.append({**s, "group": g, "flags": flags, "checked": s["name"] in chosen,
                     "usable": bool(g)})
    return render(request, "srnatoolbox/srnade_form.html", {
        "tool": tool, "bench": bench, "parent": parent, "form": form, "column": column, "rows": rows,
        "groups": parent.groups(column), "groups_on": groups_on, "far": sorted(far),
        "has_pca": (bench.directory() / "mirna_RPM_total_adj.tsv").is_file(),
        "previous": bench.children.filter(tool="srnade").order_by("-id")[:20],
        "max_age_days": settings.JOB_MAX_AGE_DAYS,
        "page_data": {"column": column, "pca_url": reverse("srnatoolbox:srnade_pca", args=[bench.slug]),
                      "samples": [{"name": r["name"], "group": r["group"]} for r in rows]}})


def _copied_settings(request, bench: Job) -> dict:
    """Initial values from an earlier sRNAde job on the same sRNAbench job (?from=<slug>)."""
    slug = request.GET.get("from", "")
    job = bench.children.filter(slug=slug, tool="srnade").first() if slug else None
    if job is None:
        return {}
    initial = dict(job.params)
    initial["samples"] = [s["name"] for s in job.inputs.get("samples", [])]
    initial["from_job"] = job.slug
    return initial


def _create_job(form: SrnadeForm, bench: Job) -> Job:
    d = form.cleaned_data
    kept = d["kept"]
    names = {n for n, _ in kept}
    job = Job.objects.create(
        app="srnatoolbox", tool="srnade", parent=bench, params=form.params(), n_items=len(kept),
        inputs={"parent": bench.slug, "dataset_title": bench.inputs.get("dataset_title", ""),
                "samples": [{"name": n, "group": g} for n, g in kept],
                "left_out": [s["name"] for s in form.parent.samples if s["name"] not in names]})
    submit(job)
    return job


@require_GET
def srnade_pca(request, slug):
    bench = _bench_job(slug)
    if bench.status != Job.DONE:
        raise Http404
    known = {s["name"] for s in bench.inputs.get("samples", [])}
    selected = [n for n in request.GET.getlist("s") if n in known]
    return JsonResponse({"pca": pca(bench, selected)})


# ---------------------------------------------------------------------------
# job page

def de_settings_rows(job: Job) -> list[tuple[str, str]]:
    p = job.params
    return [("Compared by", f"{p.get('column')}: {', '.join(p.get('groups', []))}"),
            ("RNA type", LIBRARY_LABELS.get(p.get("library_type"), p.get("library_type"))),
            ("Method", DE_METHOD_LABELS.get(p.get("method"), p.get("method"))),
            ("Significant", f"FDR < {p.get('fdr')}, |log2 fold change| ≥ {p.get('log2fc')}"),
            ("Multi-mapping reads", COUNT_LABELS.get(p.get("count_type"), p.get("count_type"))),
            ("Expression filter", f"{FILTER_LABELS.get(p.get('filter_mode'), p.get('filter_mode'))}; "
                                  f"min. read count {p.get('min_rc')}, min. RPM {p.get('min_rpm')}")]


def srnade_job_view(request, job: Job):
    remember_job(request, job)
    bench = job.parent
    context = {**status_context(job), "settings_rows": de_settings_rows(job), "bench": bench,
               "counts": _group_counts(job)}
    if bench is not None and bench.status == Job.DONE:
        context["rerun_url"] = reverse("srnatoolbox:srnade", args=[bench.slug]) + f"?from={job.slug}"
    if job.status == Job.DONE:
        context["de"] = load_de_summary(job)
    if request.htmx and not request.htmx.history_restore_request:
        response = render(request, "srnatoolbox/_srnade_status.html", context)
    else:
        response = render(request, "srnatoolbox/srnade_job.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response


def _group_counts(job: Job) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for s in job.inputs.get("samples", []):
        counts[s["group"]] = counts.get(s["group"], 0) + 1
    return sorted(counts.items())


@require_GET
def de_file(request, slug, path):
    """A file of the sRNAde output directory."""
    job = get_object_or_404(Job, slug=slug, app="srnatoolbox", tool="srnade", status=Job.DONE)
    root = (job.directory() / DE_DIR).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise Http404
    return FileResponse(open(target, "rb"), as_attachment=True, filename=target.name)
