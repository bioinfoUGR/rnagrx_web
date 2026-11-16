"""miSRA tools: forms to start a job, the job page and its result files."""
from __future__ import annotations

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import cart as cart_ops
from .accessions import resolve
from .index_store import get_catalog
from .models import Job, Selection
from .tasks import run_job
from .tools import TOOLS

SESSION_JOBS = "misra_jobs"
LOG_LINES = 40


def _source_ids(request, source: str, catalog):
    """Experiments from the cart or a saved selection: (ids, label), or (None, None) for pasted accessions."""
    if source == "cart":
        ids = cart_ops.cart_ids(cart_ops.get_cart(request))
        label = "your cart"
    elif source.startswith("selection:"):
        selection = get_object_or_404(Selection, slug=source.split(":", 1)[1])
        ids = selection.id_list()
        label = f"saved selection “{selection.name or selection.slug}”"
    else:
        return None, None
    known = sorted(str(x) for x in catalog.exp_id[catalog.positions_of(ids)])
    return known, label


def _remember_job(request, job):
    jobs = [s for s in request.session.get(SESSION_JOBS, []) if s != job.slug]
    request.session[SESSION_JOBS] = [job.slug] + jobs[:19]


@require_GET
def tools_index(request):
    slugs = request.session.get(SESSION_JOBS, [])
    jobs = sorted(Job.objects.filter(slug__in=slugs), key=lambda j: -j.pk)
    return render(request, "misra/tools.html", {"tools": TOOLS.values(), "jobs": jobs})


@require_http_methods(["GET", "POST"])
def tool_view(request, key):
    tool = TOOLS.get(key)
    if tool is None:
        raise Http404
    context = {"tool": tool, "tools": TOOLS.values()}
    if not tool.enabled:
        return render(request, "misra/tool.html", context)
    catalog = get_catalog()
    if catalog is None:
        return render(request, "misra/not_ready.html", status=503)
    source = (request.POST.get("source") if request.method == "POST" else request.GET.get("source")) or ""
    source_ids, source_label = _source_ids(request, source, catalog)
    if source_ids is None:
        source = ""
    if request.method == "POST":
        form = tool.form_class(request.POST, request.FILES)
        if form.is_valid():
            if source_ids is not None:
                ids = source_ids
            else:
                ids = resolve(form.cleaned_data["accessions"], catalog).exp_ids
            if not ids:
                form.add_error("accessions" if source_ids is None else None,
                               "No miSRA experiments found for this input.")
            elif len(ids) > tool.limit:
                form.add_error("accessions" if source_ids is None else None,
                               f"{len(ids):,} experiments: this tool accepts at most {tool.limit:,} per job. "
                               "Please split the list or narrow down your selection.")
            else:
                params = {k: form.cleaned_data.get(k) for k in tool.params}
                for key in tool.files:
                    fasta = form.cleaned_data[key]
                    params[key] = fasta.describe(fasta.filename)
                job = Job.objects.create(
                    tool=tool.key, exp_ids=" ".join(ids), n_experiments=len(ids), source=source or "accessions",
                    params=params,
                )
                if tool.files:
                    inputs = job.directory() / "input"
                    inputs.mkdir(parents=True, exist_ok=True)
                    for key in tool.files:
                        (inputs / f"{key}.fa").write_text(form.cleaned_data[key].text())
                _remember_job(request, job)
                run_job.apply_async((job.pk,), queue=tool.queue)
                return redirect(job)
    else:
        form = tool.form_class(initial={"source": source})
    context.update({"form": form, "source": source, "source_label": source_label,
                    "source_n": len(source_ids) if source_ids is not None else None})
    return render(request, "misra/tool.html", context)


@require_POST
def check_accessions(request):
    catalog = get_catalog()
    if catalog is None:
        return render(request, "misra/not_ready.html", status=503)
    tool = TOOLS.get(request.POST.get("tool", ""))
    res = resolve(request.POST.get("accessions", ""), catalog)
    return render(request, "misra/_accession_check.html", {"res": res, "limit": tool.limit if tool else None})


def _log_tail(job) -> str:
    path = job.directory() / "job.log"
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-LOG_LINES:])


@require_GET
def job_view(request, slug):
    job = get_object_or_404(Job, slug=slug)
    _remember_job(request, job)
    context = {"job": job, "tool": TOOLS.get(job.tool), "log": _log_tail(job)}
    if request.htmx and not request.htmx.history_restore_request:
        response = render(request, "misra/_job_status.html", context)
    else:
        response = render(request, "misra/job.html", context)
    patch_vary_headers(response, ("HX-Request",))
    return response


@require_GET
def job_file(request, slug, name):
    job = get_object_or_404(Job, slug=slug)
    if job.status != Job.DONE or name not in {r["name"] for r in job.results}:
        raise Http404
    path = job.directory() / name
    if not path.is_file():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=name)
