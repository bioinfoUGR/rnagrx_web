"""miSRA tools: the tool list and the forms that start a job (job pages: jobs.views)."""
from __future__ import annotations

from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from jobs.models import Job
from jobs.tasks import submit
from jobs.views import recent_jobs, remember_job

from . import cart as cart_ops
from .accessions import resolve
from .index_store import get_catalog
from .models import Selection
from .tools import TOOLS


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


@require_GET
def tools_index(request):
    return render(request, "misra/tools.html", {"tools": TOOLS.values(), "jobs": recent_jobs(request, "misra"),
                                                "max_age_days": settings.JOB_MAX_AGE_DAYS})


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
                    app="misra", tool=tool.key, params=params, n_items=len(ids),
                    inputs={"exp_ids": list(ids), "source": source or "accessions"},
                )
                if tool.files:
                    inputs = job.directory() / "input"
                    inputs.mkdir(parents=True, exist_ok=True)
                    for key in tool.files:
                        (inputs / f"{key}.fa").write_text(form.cleaned_data[key].text())
                remember_job(request, job)
                submit(job)
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
