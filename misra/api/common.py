"""Shared parts of the miSRA API: parameters, choosing experiments, output, errors, limits."""
from __future__ import annotations

import csv
import functools
import math
import time

import numpy as np
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse, QueryDict, StreamingHttpResponse
from django.urls import reverse

from ..accessions import resolve
from ..catalog import CAT_KEYS, NUM_KEYS, TAX_RANKS
from ..expression import REFERENCES, VALUES
from ..index_store import get_catalog
from ..models import ExperimentRow, Selection
from ..query import BrowseQuery
from ..textsearch import text_mask

# parameters with their own meaning; any other categorical field name is a filter (tissue=blood)
RESERVED = {"exp", "study", "selection", "taxon", "q", "reference", "value", "format", "download", "limit", "offset",
            "feature", "order_by", "rank", "term", "field", "fields", "values"}
FORMATS = ("json", "tsv", "csv")


class ApiError(Exception):
    def __init__(self, status: int, message: str, endpoint: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        self.endpoint = endpoint


def help_url(request, endpoint: str = "") -> str:
    return request.build_absolute_uri(reverse("misra:api_help")) + (f"#{endpoint}" if endpoint else "")


# ---------------------------------------------------------------------------
# request limits and CORS


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:  # behind Apache: the first address is the client
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _rate_limited(request) -> bool:
    per_minute = settings.MISRA_API_RATE_PER_MIN
    if not per_minute:
        return False
    key = f"misra:api:rate:{_client_ip(request)}:{int(time.time() // 60)}"
    if cache.add(key, 1, 70):
        return False
    try:
        return cache.incr(key) > per_minute
    except ValueError:
        return False


def endpoint(name: str):
    """Decorator: GET only, request limit, CORS, ApiError -> JSON error with help link."""

    def wrap(view):
        @functools.wraps(view)
        def inner(request, *args, **kwargs):
            if request.method == "OPTIONS":
                response = JsonResponse({})
            elif request.method != "GET":
                response = error_response(request, ApiError(405, "Only GET requests are supported.", name))
            elif _rate_limited(request):
                response = error_response(request, ApiError(
                    429, f"More than {settings.MISRA_API_RATE_PER_MIN} requests per minute. Please slow down.", name))
                response["Retry-After"] = "60"
            else:
                try:
                    response = view(request, *args, **kwargs)
                except ApiError as exc:
                    exc.endpoint = exc.endpoint or name
                    response = error_response(request, exc)
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            return response

        inner.api_name = name
        return inner

    return wrap


def error_response(request, exc: ApiError) -> JsonResponse:
    return JsonResponse({"error": exc.message, "help": help_url(request, exc.endpoint)}, status=exc.status)


# ---------------------------------------------------------------------------
# parameters


def catalog_or_error():
    catalog = get_catalog()
    if catalog is None:
        raise ApiError(503, "The miSRA index is being built. Please try again in a few minutes.")
    return catalog


def int_param(request, name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = request.GET.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, f"{name}= must be a whole number, not “{raw[:30]}”.")
    if value < minimum or (maximum is not None and value > maximum):
        raise ApiError(400, f"{name}= must be between {minimum} and {maximum}." if maximum is not None
                       else f"{name}= must be at least {minimum}.")
    return value


def choice_param(request, name: str, choices, default: str | None) -> str:
    value = request.GET.get(name) or default
    if value is None:
        raise ApiError(400, f"{name}= is required. Possible values: {', '.join(choices)}.")
    if value not in choices:
        raise ApiError(400, f"Unknown {name}= “{value[:40]}”. Possible values: {', '.join(choices)}.")
    return value


def reference_param(request, default="mirgenedb", need: str | None = None):
    choices = [k for k, r in REFERENCES.items() if not need or getattr(r, need)]
    return REFERENCES[choice_param(request, "reference", choices, default)]


def value_param(request, default="rpm_total") -> str:
    """The table column for value= (public names in expression.VALUES)."""
    return VALUES[choice_param(request, "value", list(VALUES), default)][0]


def list_param(request, name: str) -> list[str]:
    raw = " ".join(request.GET.getlist(name))
    return [t for t in raw.replace(",", " ").split() if t]


def output_format(request) -> str:
    return choice_param(request, "format", FORMATS, "json")


# ---------------------------------------------------------------------------
# choosing experiments


class Selected:
    def __init__(self, ids, unknown, evaluation, filters):
        self.ids = ids  # accessions, sorted
        self.unknown = unknown  # accessions/terms asked for but not in miSRA
        self.evaluation = evaluation  # engine Evaluation (for facet counts)
        self.mask = evaluation.mask
        self.filters = filters  # field -> values given as filters


def _browse_params(request) -> QueryDict:
    """The browse filters of the request, accepting both f_tissue= and tissue=."""
    qd = QueryDict(mutable=True)
    for key, values in request.GET.lists():
        if key.startswith("f_") or key.startswith("min_") or key.startswith("max_") or key == "q":
            qd.setlist(key, values)
        elif key in CAT_KEYS and key not in RESERVED:
            qd.setlist(f"f_{key}", qd.getlist(f"f_{key}") + values)
    return qd


def check_parameters(request) -> None:
    """Reject unknown parameter names, so a typo (tisue=blood) is not silently ignored."""
    for key in request.GET:
        if key in RESERVED or key in CAT_KEYS:
            continue
        if key.startswith("f_") and key[2:] in CAT_KEYS:
            continue
        if key.startswith(("min_", "max_")) and key[4:] in NUM_KEYS:
            continue
        raise ApiError(400, f"Unknown parameter “{key[:40]}”. Filters are the field names listed by the "
                            f"filters endpoint (e.g. tissue=, organism=, sex=), ranges are min_<field>/max_<field> "
                            f"for {', '.join(NUM_KEYS)}.")


def has_selection(request) -> bool:
    return any(k in request.GET for k in ("exp", "study", "selection", "taxon", "q")) or bool(
        [k for k in _browse_params(request) if k != "q"])


def select_experiments(request, catalog, required: bool = True, max_n: int | None = None) -> Selected:
    unknown: list[str] = []
    scope = None
    tokens = list_param(request, "exp") + list_param(request, "study")
    if tokens:
        res = resolve(" ".join(tokens), catalog)
        unknown += res.unknown_experiments + res.unknown_studies + res.invalid
        scope = catalog.mask_of_positions(catalog.positions_of(res.exp_ids))
    slug = request.GET.get("selection")
    if slug:
        sel = Selection.objects.filter(slug=slug).first()
        if sel is None:
            raise ApiError(404, f"There is no saved selection “{slug[:30]}”.")
        m = catalog.mask_of_positions(catalog.positions_of(sel.id_list()))
        scope = m if scope is None else scope | m
    taxon = (request.GET.get("taxon") or "").strip()
    taxon_mask = None
    if taxon:
        if taxon.isdigit():
            ids = list(ExperimentRow.objects.filter(taxon_id=taxon).values_list("exp_id", flat=True))
            taxon_mask = catalog.mask_of_positions(catalog.positions_of(ids))
        else:
            taxon_mask = np.zeros(catalog.n, dtype=bool)
            for rank in TAX_RANKS + ["tax_species"]:
                hits = [v for v in catalog.vocab[rank] if v and str(v).casefold() == taxon.casefold()]
                if hits:
                    taxon_mask |= catalog.facet_mask(rank, hits)
        if not taxon_mask.any():
            raise ApiError(404, f"No experiments for taxon “{taxon[:60]}” (NCBI taxon id or a name such as "
                                f"Homo sapiens, Mammalia, Viridiplantae).")
        scope = taxon_mask if scope is None else scope & taxon_mask

    check_parameters(request)
    bq = BrowseQuery.from_params(_browse_params(request))
    if required and scope is None and not bq.filters and not bq.ranges and not bq.text:
        raise ApiError(400, "Choose experiments with exp=, study=, selection=, taxon= or filters "
                            "(e.g. organism=Homo sapiens&tissue=blood).")
    q = bq.engine_query()
    q.scope_mask = scope
    if bq.text:
        q.text_mask = text_mask(catalog, bq.text)
    evaluation = catalog.evaluate(q)
    ids = catalog.exp_ids(evaluation.mask)
    if max_n is not None and len(ids) > max_n:
        raise ApiError(413, f"{len(ids):,} experiments selected; this endpoint returns at most {max_n:,} per "
                            "request. Narrow the selection, or use the Expression matrix tool for large sets.")
    return Selected(ids, unknown, evaluation, bq.filters)


# ---------------------------------------------------------------------------
# output


def clean(value):
    """JSON-safe: NaN/inf -> None, numpy scalars -> Python."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def json_response(payload: dict, request=None, filename: str = "misra.json") -> JsonResponse:
    response = JsonResponse(clean(payload), json_dumps_params={"allow_nan": False})
    if request is not None and request.GET.get("download"):
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class _Echo:
    def write(self, value):
        return value


def _cell(v):
    v = clean(v)
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def table_response(request, columns, rows, filename: str) -> StreamingHttpResponse:
    """TSV or CSV from a header and an iterable of rows."""
    fmt = output_format(request)
    sep = "\t" if fmt == "tsv" else ","

    def lines():
        writer = csv.writer(_Echo(), delimiter=sep, lineterminator="\n")
        yield writer.writerow(columns)
        for row in rows:
            yield writer.writerow([_cell(v) for v in row])

    content_type = "text/tab-separated-values" if fmt == "tsv" else "text/csv"
    response = StreamingHttpResponse(lines(), content_type=f"{content_type}; charset=utf-8")
    disposition = "attachment" if request.GET.get("download") else "inline"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}.{fmt}"'
    return response
