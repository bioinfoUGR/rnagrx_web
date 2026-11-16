from __future__ import annotations

import csv
import math
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.cache import patch_vary_headers
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from misradb.models import Experiment, ExperimentAnalysis, ReadFile, Study

from . import cart as cart_ops
from .catalog import FIELD_BY_KEY, NUM_KEYS, TAX_RANKS
from .engine import SORTS, Catalog
from .index_store import current_build, get_catalog
from .models import ExperimentRow, FacetDefinition, Selection, StudyRow
from .query import STUDY_SORTS, TABS, BrowseQuery
from .textsearch import text_mask

# ---------------------------------------------------------------------------
# helpers


def _catalog_or_none(request):
    catalog = get_catalog()
    if catalog is None:
        return None, render(request, "misra/not_ready.html", status=503)
    return catalog, None


def _facet_definitions():
    return list(FacetDefinition.objects.filter(enabled=True))


def _scope(request, catalog: Catalog, scope: str, selection: Selection | None):
    if scope == "cart":
        return cart_ops.cart_mask(cart_ops.get_cart(request), catalog)
    if scope == "selection":
        return catalog.mask_of_positions(catalog.positions_of(selection.id_list()))
    return None


def _evaluate(request, catalog, bq: BrowseQuery, scope="all", selection=None):
    q = bq.engine_query()
    q.scope_mask = _scope(request, catalog, scope, selection)
    if bq.text:
        q.text_mask = text_mask(catalog, bq.text)
    return catalog.evaluate(q)


def _facets(catalog, ev, bq: BrowseQuery, definitions):
    facets = []
    for d in definitions:
        if d.widget == "range":
            if d.key not in NUM_KEYS:
                continue
            lo, hi = bq.ranges.get(d.key, (None, None))
            facets.append({"def": d, "range": True, "min": lo, "max": hi})
            continue
        if d.key not in catalog.codes:
            continue
        selected = bq.filters.get(d.key, [])
        values, n_values = catalog.facet_values(ev, d.key, selected=selected, limit=d.top_n,
                                                hidden=d.hidden_values or ())
        facets.append({"def": d, "range": False, "values": values, "n_values": n_values,
                       "more": n_values > len([v for v in values if v["count"]]),
                       "open": d.open_by_default or bool(selected)})
    return facets


def _browse_context(request, catalog, bq: BrowseQuery, scope="all", selection=None):
    ev = _evaluate(request, catalog, bq, scope, selection)
    page_size = settings.MISRA_PAGE_SIZE
    total = ev.count
    if bq.tab == "studies":
        rows, n_rows = catalog.study_page(ev.mask, bq.sort, bq.desc, (bq.page - 1) * page_size, page_size)
    else:
        n_rows = total
        rows = catalog.rows(catalog.page(ev.mask, bq.sort, bq.desc, (bq.page - 1) * page_size, page_size))
    n_pages = max(1, math.ceil(n_rows / page_size))
    if scope == "cart":
        base_url = reverse("misra:cart")
    elif scope == "selection":
        base_url = reverse("misra:selection", args=[selection.slug])
    else:
        base_url = reverse("misra:browse")
    n_studies = int((catalog.study_page(ev.mask, limit=0)[1])) if bq.tab == "experiments" else n_rows
    return {
        "bq": bq,
        "scope": scope,
        "selection": selection,
        "base_url": base_url,
        "facets": _facets(catalog, ev, bq, _facet_definitions()),
        "chips": bq.chips(),
        "rows": rows,
        "total": total,
        "n_studies": n_studies,
        "n_rows": n_rows,
        "page": bq.page,
        "n_pages": n_pages,
        "page_links": [(p, bq.urlencode(page=p) if p else None) for p in _page_window(bq.page, n_pages)],
        "prev_link": bq.urlencode(page=bq.page - 1) if bq.page > 1 else None,
        "next_link": bq.urlencode(page=bq.page + 1) if bq.page < n_pages else None,
        "tab_links": {t: bq.urlencode(tab=t) for t in TABS},
        "sort_links": {s: bq.sort_link(s) for s in (STUDY_SORTS if bq.tab == "studies" else SORTS)},
        "clear_link": BrowseQuery(tab=bq.tab).urlencode(),
        "page_size": page_size,
        "filter_params": bq.filter_params(),
        "cart_limit": settings.MISRA_CART_LIMIT,
        "fields": FIELD_BY_KEY,
    }


def _page_window(page, n_pages, width=2):
    pages = sorted({1, n_pages, *range(max(1, page - width), min(n_pages, page + width) + 1)})
    out, prev = [], 0
    for p in pages:
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def _render_browse(request, template, context):
    if request.htmx and not request.htmx.history_restore_request:
        response = render(request, "misra/_browse.html", context)
    else:
        response = render(request, template, context)
    patch_vary_headers(response, ("HX-Request",))
    return response


def _toast(request, text, level="info", status=200):
    response = render(request, "misra/_toast.html", {"text": text, "level": level,
                                                     "cart_count": cart_ops.cart_count(cart_ops.get_cart(request))})
    response.status_code = status
    return response


# ---------------------------------------------------------------------------
# pages


@require_GET
def home(request):
    build = current_build()
    return render(request, "misra/home.html", {"build": build, "stats": build.stats if build else {}})


@require_GET
def browse(request):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    bq = BrowseQuery.from_params(request.GET)
    return _render_browse(request, "misra/browse.html", _browse_context(request, catalog, bq))


@require_GET
def facet_values(request, key):
    """All values of one facet matching a search term (the 'show more' box)."""
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    if key not in catalog.codes:
        raise Http404
    bq = BrowseQuery.from_params(request.GET)
    scope = request.GET.get("scope", "all")
    selection = None
    if scope == "selection":
        selection = get_object_or_404(Selection, slug=request.GET.get("sel", ""))
    elif scope != "cart":
        scope = "all"
    ev = _evaluate(request, catalog, bq, scope, selection)
    definition = FacetDefinition.objects.filter(key=key).first()
    values, n_values = catalog.facet_values(ev, key, selected=bq.filters.get(key, []), limit=200,
                                            term=request.GET.get("term", "").strip()[:100],
                                            hidden=(definition.hidden_values if definition else ()) or ())
    return render(request, "misra/_facet_more.html", {"key": key, "values": values, "n_values": n_values,
                                                      "limit": 200})


TAX_SORTS = ("name", "organisms", "studies", "experiments", "reads")
TAX_TABLE_LIMIT = 1000


@require_GET
def taxonomy(request):
    """Browse by taxonomy: a table of taxa at one rank (kingdom ... organism) with the number of
    organisms, studies, experiments and reads; search it, drill down, tick taxa and go on to
    their experiments."""
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    bq = BrowseQuery.from_params(request.GET)
    path = [(r, bq.filters[r][0]) for r in TAX_RANKS if bq.filters.get(r)]  # parent taxa, top-down
    path_filters = {r: [v] for r, v in path}
    deepest = max((TAX_RANKS.index(r) for r, _ in path), default=-1)
    ranks = TAX_RANKS[deepest + 1:] or TAX_RANKS[-1:]
    rank = request.GET.get("rank")
    if rank not in ranks:
        rank = ranks[0]
    term = (request.GET.get("term") or "").strip()[:100]
    sort = request.GET.get("sort")
    sort = sort if sort in TAX_SORTS else "experiments"
    desc = request.GET.get("dir", "asc" if sort == "name" else "desc") == "desc"

    ev = catalog.evaluate(BrowseQuery(filters=path_filters).engine_query())
    parents = TAX_RANKS[:TAX_RANKS.index(rank)]
    rows = catalog.taxon_table(ev.mask, rank, [p for p in parents if p not in path_filters])
    n_all = len(rows)
    if term:
        t = term.casefold()
        rows = [r for r in rows if t in r["value"].casefold() or any(t in x.casefold() for x in r["lineage"])]
    key = "value" if sort == "name" else sort
    rows.sort(key=lambda r: (r[key].casefold() if key == "value" else r[key], r["value"]), reverse=desc)
    if sort == "name":  # "not assigned" last
        rows.sort(key=lambda r: r["value"] == "")
    n_rows = len(rows)
    rows = rows[:TAX_TABLE_LIMIT]

    def params(**extra):
        items = [(f"f_{r}", v) for r, v in path]
        items += [(k, v) for k, v in extra.items() if v not in (None, "")]
        return urlencode(items)

    next_rank = ranks[ranks.index(rank) + 1] if rank != TAX_RANKS[-1] and ranks.index(rank) + 1 < len(ranks) else None
    for r in rows:
        r["browse"] = urlencode([(f"f_{p}", v) for p, v in path] + [(f"f_{rank}", r["value"])])
        r["drill"] = (urlencode([(f"f_{p}", v) for p, v in path] + [(f"f_{rank}", r["value"]),
                                                                  ("rank", next_rank)]) if next_rank else None)
    crumbs = [{"rank": FIELD_BY_KEY[r].label, "value": v or "not assigned",
               "params": urlencode([(f"f_{p}", pv) for p, pv in path[:i + 1]])}
              for i, (r, v) in enumerate(path)]
    sort_links = {}
    for name in TAX_SORTS:
        if name == sort:  # clicking the current column flips the direction
            direction = "asc" if desc else "desc"
        else:
            direction = "asc" if name == "name" else "desc"
        sort_links[name] = params(rank=rank, term=term, sort=name, dir=direction)
    context = {
        "rank": rank, "rank_label": FIELD_BY_KEY[rank].label,
        "rank_tabs": [{"key": r, "label": FIELD_BY_KEY[r].label, "params": params(rank=r, term=term),
                       "active": r == rank} for r in ranks],
        "crumbs": crumbs, "path": path, "term": term, "rows": rows, "n_rows": n_rows, "n_all": n_all,
        "limit": TAX_TABLE_LIMIT, "sort": sort, "desc": desc, "sort_links": sort_links,
        "total": ev.count, "browse_params": params(), "show_lineage": bool(parents) and rank != TAX_RANKS[0],
        "lineage_label": " › ".join(FIELD_BY_KEY[p].label for p in parents if p not in path_filters),
        "is_organism": rank == "organism",
    }
    template = "misra/_taxonomy.html" if request.htmx and not request.htmx.history_restore_request \
        else "misra/taxonomy.html"
    response = render(request, template, context)
    patch_vary_headers(response, ("HX-Request",))
    return response


@require_GET
def experiment_detail(request, exp_id):
    exp = Experiment.objects.select_related("study").filter(pk=exp_id).first()
    if exp is None:
        raise Http404
    row = ExperimentRow.objects.filter(pk=exp_id).first()
    analysis = ExperimentAnalysis.objects.filter(pk=exp_id).only("inferred_metadata").first()
    inferred = (analysis.inferred_metadata or {}) if analysis else {}
    tags = {k: v for k, v in (inferred.get("secondary_tags") or {}).items() if v not in (None, "")}
    other = inferred.get("other_annotations") or {}
    files = list(ReadFile.objects.filter(exp_id=exp_id))
    in_cart = exp_id in set(cart_ops.cart_ids(cart_ops.get_cart(request)))
    return render(request, "misra/experiment.html", {"exp": exp, "row": row, "tags": tags, "other": other,
                                                     "files": files, "in_cart": in_cart})


@require_GET
def study_detail(request, study_id):
    study = Study.objects.filter(pk=study_id).first()
    if study is None:
        raise Http404
    srow = StudyRow.objects.filter(pk=study_id).first()
    pubmed = study.pubmed_ids if isinstance(study.pubmed_ids, list) else ([study.pubmed_ids] if study.pubmed_ids else [])
    grouping = study.grouping_vars if isinstance(study.grouping_vars, dict) else {}
    return render(request, "misra/study.html", {
        "study": study, "srow": srow, "pubmed": pubmed, "grouping": grouping,
        "browse_params": BrowseQuery(filters={"study": [study_id]}).urlencode(),
    })


@require_GET
def explore(request):
    build = current_build()
    stats = build.stats if build else {}
    panels = []
    labels = {"organism": "Experiments per organism", "tax_kingdom": "Experiments per kingdom",
              "sample_class": "Sample class", "tissue": "Tissue", "cell_line": "Cell line",
              "disease_state": "Disease state", "protocol": "Library protocol (inferred)",
              "availability": "Processing results"}
    for key, title in labels.items():
        items = (stats.get("top") or {}).get(key) or []
        peak = max((c for _, c in items), default=1)
        panels.append({"key": key, "title": title,
                       "items": [{"label": v, "count": c, "pct": 100 * c / peak,
                                  "params": BrowseQuery(filters={key: [v]}).urlencode()} for v, c in items]})
    sizes = stats.get("study_sizes") or []
    peak = max((c for _, c in sizes), default=1) or 1
    annotated = [{"label": FIELD_BY_KEY[k].label, "count": n,
                  "pct": 100 * n / max(1, stats.get("n_experiments", 1))}
                 for k, n in (stats.get("annotated") or {}).items() if k in FIELD_BY_KEY and not k.startswith("tax_")
                 and k != "study"]
    return render(request, "misra/explore.html", {
        "build": build, "stats": stats, "panels": panels,
        "sizes": [{"label": label, "count": c, "pct": 100 * c / peak} for label, c in sizes],
        "annotated": sorted(annotated, key=lambda a: -a["count"]),
    })


@require_GET
def about(request):
    return render(request, "misra/about.html")


@require_GET
def docs(request):
    return render(request, "misra/docs.html", {"cart_limit": settings.MISRA_CART_LIMIT,
                                               "cart_days": settings.MISRA_CART_MAX_AGE_DAYS})


# ---------------------------------------------------------------------------
# cart


@require_GET
def cart_view(request):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    bq = BrowseQuery.from_params(request.GET)
    context = _browse_context(request, catalog, bq, scope="cart")
    context["cart_total"] = cart_ops.cart_count(cart_ops.get_cart(request))
    context["cart_days"] = settings.MISRA_CART_MAX_AGE_DAYS
    return _render_browse(request, "misra/cart.html", context)


def _posted_selection(request, catalog):
    """Experiments a cart action refers to: ticked rows (mode=ids), or everything matching
    the posted filters (mode=query), optionally limited to ticked studies."""
    mode = request.POST.get("mode")
    if mode == "ids":
        return [e for e in request.POST.getlist("exp") if e][: settings.MISRA_CART_LIMIT]
    if mode == "query":
        bq = BrowseQuery.from_params(request.POST)
        scope = request.POST.get("scope", "all")
        selection = None
        if scope == "selection":
            selection = get_object_or_404(Selection, slug=request.POST.get("sel", ""))
        elif scope != "cart":
            scope = "all"
        ev = _evaluate(request, catalog, bq, scope, selection)
        return catalog.exp_ids(ev.mask)
    return None


@require_POST
def cart_add(request):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    ids = _posted_selection(request, catalog)
    if ids is None:
        return HttpResponseBadRequest("unknown mode")
    if not ids:
        return _toast(request, "Nothing selected: tick experiments first.", "warning")
    cart = cart_ops.get_cart(request, create=True)
    added, truncated = cart_ops.add(cart, ids)
    already = len(ids) - added if not truncated else None
    text = f"{added:,} experiment{'s' if added != 1 else ''} added to the cart."
    if already:
        text += f" {already:,} {'was' if already == 1 else 'were'} already in it."
    if truncated:
        text += f" The cart is limited to {settings.MISRA_CART_LIMIT:,} experiments; the rest were not added."
    return _toast(request, text, "warning" if truncated else "info")


def _cart_changed(request, text):
    messages.info(request, text)
    response = HttpResponse(status=204)
    response["HX-Refresh"] = "true"
    return response


@require_POST
def cart_remove(request):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    cart = cart_ops.get_cart(request)
    ids = _posted_selection(request, catalog) if cart else []
    if ids is None:
        return HttpResponseBadRequest("unknown mode")
    if not ids:
        return _toast(request, "Nothing selected.", "warning")
    n = cart_ops.remove(cart, ids)
    return _cart_changed(request, f"{n:,} experiment{'s' if n != 1 else ''} removed from the cart.")


@require_POST
def cart_keep(request):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    cart = cart_ops.get_cart(request)
    if not cart:
        return _toast(request, "The cart is empty.", "warning")
    ids = _posted_selection(request, catalog)
    if ids is None:
        return HttpResponseBadRequest("unknown mode")
    n = cart_ops.keep_only(cart, ids)
    return _cart_changed(request, f"Kept {len(ids):,} matching experiments; removed {n:,}.")


@require_POST
def cart_clear(request):
    cart = cart_ops.get_cart(request)
    n = cart_ops.clear(cart) if cart else 0
    return _cart_changed(request, f"Cart emptied ({n:,} experiments removed).")


@require_POST
def cart_save(request):
    cart = cart_ops.get_cart(request)
    ids = cart_ops.cart_ids(cart)
    if not ids:
        return _toast(request, "The cart is empty: nothing to save.", "warning")
    sel = Selection.objects.create(
        name=request.POST.get("name", "").strip()[:200],
        description=request.POST.get("description", "").strip()[:2000],
        exp_ids=" ".join(sorted(ids)),
        n_experiments=len(ids),
    )
    response = HttpResponse(status=204)
    response["HX-Redirect"] = sel.get_absolute_url()
    return response


# ---------------------------------------------------------------------------
# saved selections


@require_GET
def selection_view(request, slug):
    catalog, error = _catalog_or_none(request)
    if error:
        return error
    selection = get_object_or_404(Selection, slug=slug)
    bq = BrowseQuery.from_params(request.GET)
    context = _browse_context(request, catalog, bq, scope="selection", selection=selection)
    context["missing"] = selection.n_experiments - len(catalog.positions_of(selection.id_list()))
    return _render_browse(request, "misra/selection.html", context)


# ---------------------------------------------------------------------------
# downloads

TSV_FIELDS = [f.name for f in ExperimentRow._meta.concrete_fields if f.name not in ("search_text", "created_at")]


class _Echo:
    def write(self, value):
        return value


def _tsv_response(ids, filename):
    def rows():
        writer = csv.writer(_Echo(), delimiter="\t", lineterminator="\n")
        yield writer.writerow(TSV_FIELDS)
        ids_sorted = sorted(ids)
        for i in range(0, len(ids_sorted), 2000):
            chunk = ExperimentRow.objects.filter(exp_id__in=ids_sorted[i:i + 2000]).order_by("exp_id")
            for values in chunk.values_list(*TSV_FIELDS):
                yield writer.writerow(["" if v is None else (int(v) if isinstance(v, float) and v.is_integer() else v)
                                       for v in values])
    response = StreamingHttpResponse(rows(), content_type="text/tab-separated-values; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@require_GET
def cart_tsv(request):
    ids = cart_ops.cart_ids(cart_ops.get_cart(request))
    if not ids:
        return redirect("misra:cart")
    return _tsv_response(ids, "misra_cart_metadata.tsv")


@require_GET
def selection_tsv(request, slug):
    selection = get_object_or_404(Selection, slug=slug)
    name = slugify(selection.name) or selection.slug
    return _tsv_response(selection.id_list(), f"misra_{name}_metadata.tsv")
