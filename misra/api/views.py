"""miSRA API v1 (read-only, GET). Documentation: misra/api/docs.py and the page at /misra/api/."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET

from misradb.models import Study

from ..catalog import CAT_KEYS, FIELD_BY_KEY, NUM_KEYS, TAX_RANKS
from ..export import TSV_FIELDS
from ..expression import REFERENCES, VALUE_BY_COLUMN, VALUES, load_feature, load_values, matrix, parse_json_column
from ..index_store import current_build
from ..models import ExperimentRow, FacetDefinition, Selection, StudyRow
from .common import (FORMATS, ApiError, catalog_or_error, choice_param, endpoint, has_selection, help_url,
                     int_param, json_response, list_param, output_format, reference_param, select_experiments,
                     table_response, value_param)
from .docs import ENDPOINTS, FORMAT_PARAMS, SELECT_PARAMS

META_FIELDS = ["study", "organism", "sample_class", "tissue", "cell_type", "cell_line", "disease_state", "sex",
               "developmental_stage", "protocol", "layout", "availability"]
RANKS = {"kingdom": "tax_kingdom", "phylum": "tax_phylum", "class": "tax_class", "order": "tax_order",
         "family": "tax_family", "genus": "tax_genus", "organism": "organism"}


def _limits():
    return settings.MISRA_API_MATRIX_LIMIT, settings.MISRA_API_DETAIL_LIMIT


def _in(n):
    return ", ".join(["%s"] * n)


def _metadata(catalog, ids) -> dict[str, dict]:
    rows = catalog.rows(catalog.positions_of(ids))
    return {r["exp_id"]: {k: (r[k] or None) for k in META_FIELDS} for r in rows}


def _analysis_json(ids, column_sql: str) -> dict[str, object]:
    """exp_id -> parsed JSON from experiment_analysis (column_sql is a fixed expression, never user input)."""
    out = {}
    with connections["misra"].cursor() as cur:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            cur.execute(f"SELECT experiment_id, {column_sql} FROM experiment_analysis "
                        f"WHERE experiment_id IN ({_in(len(chunk))})", chunk)
            for exp_id, value in cur.fetchall():
                out[exp_id] = parse_json_column(value)
    return out


def _num(v, digits: int = 4):
    """Number from the pipeline's JSON (often strings like "8403.999999999916"): negative (= not available)
    -> None, integral -> int, otherwise rounded."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x < 0:
        return None
    if abs(x - round(x)) < 1e-6:
        return int(round(x))
    return round(x, digits)


def _envelope(sel, **data):
    payload = dict(data)
    if sel is not None and sel.unknown:
        payload["not_found"] = sel.unknown
    return payload


# ---------------------------------------------------------------------------


@endpoint("database")
def database(request):
    build = current_build()
    stats = build.stats if build else {}
    api_base = request.build_absolute_uri(reverse("misra:api_help")) + "v1/"
    return json_response({
        "name": "miSRA",
        "build": build.finished_at.isoformat() if build and build.finished_at else None,
        "experiments": stats.get("n_experiments"), "studies": stats.get("n_studies"),
        "organisms": stats.get("n_organisms"),
        "references": {k: {"label": r.label, "features": r.feature, "isomirs": r.has_isomirs,
                           "seeds": r.has_seeds, "trna_fragments": r.has_fragments} for k, r in REFERENCES.items()},
        "values": {k: d for k, (_, d) in VALUES.items()},
        "filters": [k for k in CAT_KEYS], "ranges": NUM_KEYS,
        "endpoints": [{"name": e.name, "summary": e.summary, "url": api_base + e.name,
                       "help": help_url(request, e.name)} for e in ENDPOINTS],
        "help": help_url(request),
    })


@endpoint("experiments")
def experiments(request):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, required=False)
    fields = list_param(request, "fields") or TSV_FIELDS
    bad = [f for f in fields if f not in TSV_FIELDS]
    if bad:
        raise ApiError(400, f"Unknown field(s) {', '.join(bad[:5])}. Available: {', '.join(TSV_FIELDS)}.")
    fmt = output_format(request)
    offset = int_param(request, "offset", 0)
    if fmt == "json":
        limit = int_param(request, "limit", 100, 0, 10_000)
    else:
        limit = int_param(request, "limit", len(sel.ids), 0)
    page = sel.ids[offset:offset + limit]

    def rows():
        for i in range(0, len(page), 2000):
            chunk = page[i:i + 2000]
            found = {r[0]: r for r in ExperimentRow.objects.filter(exp_id__in=chunk).values_list("exp_id", *fields)}
            for e in chunk:
                if e in found:
                    yield found[e][1:]

    if fmt == "json":
        return json_response(_envelope(sel, count=len(sel.ids), offset=offset, limit=limit,
                                       experiments=[dict(zip(fields, r)) for r in rows()]),
                             request, "misra_experiments.json")
    return table_response(request, fields, rows(), "misra_experiments")


@endpoint("experiment-summary")
def experiment_summary(request):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, max_n=_limits()[1])
    meta = _metadata(catalog, sel.ids)
    stats = {}
    with connections["misra"].cursor() as cur:
        for i in range(0, len(sel.ids), 500):
            chunk = sel.ids[i:i + 500]
            cur.execute("SELECT id, raw_read_count, adapter_dimer_count, short_read_count, read_count, mirna_percent, "
                        "trna_percent, rrna_percent, perc_genome_mapped, perc_bacteria_mapped, perc_virus_mapped "
                        f"FROM experiments WHERE id IN ({_in(len(chunk))})", chunk)
            for row in cur.fetchall():
                stats[row[0]] = row[1:]
    preproc = _analysis_json(sel.ids, "JSON_EXTRACT(preproc_results, '$.misra_preproc')")
    out = {}
    for e in sel.ids:
        s = stats.get(e)
        if s is None:
            continue
        pre = {k: v for k, v in (preproc.get(e) or {}).items() if k not in ("exp", "taxon_id", "files")}
        out[e] = {
            "metadata": meta.get(e, {}),
            "reads": {"raw_reads": _num(s[0]), "adapter_dimers": _num(s[1]), "too_short": _num(s[2]),
                      "reads_in_analysis": _num(s[3])},
            "percent": {"mirna": _num(s[4]), "trna": _num(s[5]), "rrna": _num(s[6]), "genome": _num(s[7]),
                        "bacteria": _num(s[8]), "virus": _num(s[9])},
            "preprocessing": pre,
        }
    if output_format(request) == "json":
        return json_response(_envelope(sel, experiments=out), request, "misra_experiment_summary.json")
    pre_keys = sorted({k for v in out.values() for k in v["preprocessing"]})
    columns = (["experiment"] + META_FIELDS + [f"reads_{k}" for k in ("raw_reads", "adapter_dimers", "too_short",
                                                                       "reads_in_analysis")]
               + [f"percent_{k}" for k in ("mirna", "trna", "rrna", "genome", "bacteria", "virus")]
               + [f"preprocessing_{k}" for k in pre_keys])
    rows = ([e] + [v["metadata"].get(k) for k in META_FIELDS] + list(v["reads"].values()) + list(v["percent"].values())
            + [v["preprocessing"].get(k) for k in pre_keys] for e, v in out.items())
    return table_response(request, columns, rows, "misra_experiment_summary")


def _json_lists(request, sel, column_sql, convert, columns, filename):
    data = _analysis_json(sel.ids, column_sql)
    out = {e: convert(data[e]) for e in sel.ids if data.get(e) is not None}
    if output_format(request) == "json":
        return json_response(_envelope(sel, experiments=out), request, f"{filename}.json")
    rows = ([e] + [r.get(c) for c in columns] for e, items in out.items() for r in items)
    return table_response(request, ["experiment"] + columns, rows, filename)


@endpoint("rna-composition")
def rna_composition(request):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, max_n=_limits()[1])
    columns = ["rna_type", "unique_reads", "unique_reads_percent", "read_count", "read_count_percent"]

    def convert(items):
        return [{"rna_type": r.get("rna_type"), "unique_reads": _num(r.get("ur")),
                 "unique_reads_percent": _num(r.get("ur_perc")), "read_count": _num(r.get("rc")),
                 "read_count_percent": _num(r.get("rc_perc"))} for r in (items or [])]

    return _json_lists(request, sel, "profiling_results", convert, columns, "misra_rna_composition")


@endpoint("read-lengths")
def read_lengths(request):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, max_n=_limits()[1])
    columns = ["length", "unique_reads", "unique_reads_percent", "read_count", "read_count_percent", "rpm"]

    def convert(data):
        items = data.get("read_length", []) if isinstance(data, dict) else []
        return [{"length": _num(r.get("Read length")), "unique_reads": _num(r.get("ur")),
                 "unique_reads_percent": _num(r.get("ur_perc")), "read_count": _num(r.get("rc")),
                 "read_count_percent": _num(r.get("rc_perc")), "rpm": _num(r.get("rpm"))} for r in items
                if _num(r.get("rc"))]

    return _json_lists(request, sel, "read_lengths", convert, columns, "misra_read_lengths")


def _matrix_response(request, sel, ref, m: pd.DataFrame, missing, value_name, index_label, filename):
    m = m.round(4)
    if output_format(request) == "json":
        return json_response(_envelope(sel, reference=ref.key, value=value_name, features=list(m.index),
                                       experiments=list(m.columns), data=m.values.tolist(),
                                       experiments_without_values=missing), request, f"{filename}.json")
    rows = ([f] + list(vals) for f, vals in zip(m.index, m.values.tolist()))
    return table_response(request, [index_label] + list(m.columns), rows, filename)


@endpoint("expression-matrix")
def expression_matrix(request):
    catalog = catalog_or_error()
    ref = reference_param(request)
    col = value_param(request)
    sel = select_experiments(request, catalog, max_n=_limits()[0])
    data = load_values(ref, sel.ids, (col,))
    features = list_param(request, "feature")
    if features:
        data = data[data["feature"].isin(features)]
    m, missing = matrix(data, col, sel.ids)
    return _matrix_response(request, sel, ref, m, missing, VALUE_BY_COLUMN[col], ref.feature,
                            f"misra_matrix_{ref.key}_{VALUE_BY_COLUMN[col]}")


@endpoint("seed-expression")
def seed_expression(request):
    catalog = catalog_or_error()
    ref = reference_param(request, need="has_seeds")
    col = value_param(request)
    sel = select_experiments(request, catalog, max_n=_limits()[0])
    data = load_values(ref, sel.ids, (col, "seed"))
    data = data[data["seed"].notna() & (data["seed"] != "")]
    m, missing = matrix(data, col, sel.ids, index="seed")
    return _matrix_response(request, sel, ref, m, missing, VALUE_BY_COLUMN[col], "seed",
                            f"misra_seeds_{ref.key}_{VALUE_BY_COLUMN[col]}")


def _nested_classes(request, ref, column, convert, columns, filename):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, max_n=_limits()[1])
    data = load_values(ref, sel.ids, (column,))
    features = list_param(request, "feature")
    if features:
        data = data[data["feature"].isin(features)]
    out: dict[str, dict] = {}
    for exp_id, feature, raw in data.itertuples(index=False):
        classes = parse_json_column(raw)
        if classes:
            out.setdefault(exp_id, {})[feature] = convert(classes)
    out = {e: out[e] for e in sel.ids if e in out}
    if output_format(request) == "json":
        return json_response(_envelope(sel, reference=ref.key, experiments=out), request, f"{filename}.json")
    rows = ([e, f, cls] + vals for e, feats in out.items() for f, classes in feats.items()
            for cls, vals in _class_rows(classes, columns))
    return table_response(request, ["experiment", ref.feature, "class"] + columns, rows, filename)


def _class_rows(classes, columns):
    for cls, v in classes.items():
        yield cls, ([v.get(c) for c in columns] if isinstance(v, dict) else [v])


@endpoint("isomirs")
def isomirs(request):
    ref = reference_param(request, need="has_isomirs")
    return _nested_classes(request, ref, "iso_mirs", lambda c: {k: _num(v) for k, v in c.items()},
                           ["read_count"], f"misra_isomirs_{ref.key}")


@endpoint("trna-fragments")
def trna_fragments(request):
    ref = reference_param(request, default="trna-nuclear", need="has_fragments")
    names = {"rc": "read_count", "rcadj": "adjusted_read_count", "rc_perc": "read_count_percent",
             "rcadj_perc": "adjusted_read_count_percent"}

    def convert(classes):
        return {cls: {names.get(k, k): _num(v) for k, v in vals.items()} for cls, vals in classes.items()
                if isinstance(vals, dict)}

    return _nested_classes(request, ref, "fragment_class", convert, list(names.values()),
                           f"misra_trna_fragments_{ref.key}")


@endpoint("top-experiments")
def top_experiments(request):
    catalog = catalog_or_error()
    ref = reference_param(request)
    feature = (request.GET.get("feature") or "").strip()
    if not feature:
        raise ApiError(400, "feature= is required (one miRNA or tRNA name; see the feature-names endpoint).")
    order = VALUES[choice_param(request, "order_by", list(VALUES), "rpm_total")][0]
    limit = int_param(request, "limit", 20, 1, 1000)
    df = load_feature(ref, feature)
    if df.empty:
        raise ApiError(404, f"“{feature[:60]}” has no reads in any experiment for reference {ref.key}. "
                            "Names are case-sensitive; see the feature-names endpoint.")
    sel = None
    if has_selection(request):
        sel = select_experiments(request, catalog)
        df = df[df["exp_id"].isin(set(sel.ids))]
    df = df.sort_values([order, "exp_id"], ascending=[False, True]).head(limit)
    meta = _metadata(catalog, list(df["exp_id"]))
    rows = [{"experiment": r.exp_id, **meta.get(r.exp_id, {}),
             **{name: _num(getattr(r, col)) for name, (col, _) in VALUES.items()}} for r in df.itertuples(index=False)]
    if output_format(request) == "json":
        return json_response(_envelope(sel, feature=feature, reference=ref.key, order_by=VALUE_BY_COLUMN[order],
                                       experiments=rows), request, f"misra_top_{feature}.json")
    columns = ["experiment"] + META_FIELDS + list(VALUES)
    return table_response(request, columns, ([r.get(c) for c in columns] for r in rows), f"misra_top_{feature}")


@endpoint("expression-summary")
def expression_summary(request):
    catalog = catalog_or_error()
    ref = reference_param(request)
    col = value_param(request)
    features = list_param(request, "feature")
    if not features:
        raise ApiError(400, "feature= is required (miRNA/tRNA names, comma-separated).")
    if len(features) > 100:
        raise ApiError(400, "At most 100 features per request.")
    sel = select_experiments(request, catalog) if has_selection(request) else None
    selected = set(sel.ids) if sel else None
    n_selected = len(sel.ids) if sel else catalog.n
    out, unknown = {}, []
    for f in dict.fromkeys(features):
        df = load_feature(ref, f)
        if df.empty:
            unknown.append(f)
            continue
        if selected is not None:
            df = df[df["exp_id"].isin(selected)]
        vals = df[col].fillna(0)
        detected = df[vals > 0]
        top = detected.loc[detected[col].idxmax()] if len(detected) else None
        out[f] = {
            "experiments_selected": n_selected,
            "experiments_with_reads": int(len(detected)),
            "percent_with_reads": round(100 * len(detected) / n_selected, 3) if n_selected else None,
            "sum": _num(vals.sum()),
            "mean_all": _num(vals.sum() / n_selected) if n_selected else None,
            "mean_with_reads": _num(detected[col].mean()) if len(detected) else None,
            "median_with_reads": _num(detected[col].median()) if len(detected) else None,
            "max": _num(top[col]) if top is not None else None,
            "max_experiment": top["exp_id"] if top is not None else None,
        }
    payload = _envelope(sel, reference=ref.key, value=VALUE_BY_COLUMN[col], features=out)
    if unknown:
        payload["unknown_features"] = unknown
    if output_format(request) == "json":
        return json_response(payload, request, "misra_expression_summary.json")
    columns = ["experiments_selected", "experiments_with_reads", "percent_with_reads", "sum", "mean_all",
               "mean_with_reads", "median_with_reads", "max", "max_experiment"]
    return table_response(request, [ref.feature] + columns, ([f] + [v[c] for c in columns] for f, v in out.items()),
                          "misra_expression_summary")


def _feature_names(ref) -> list[tuple[str, int]]:
    build = current_build()
    key = f"misra:api:names:{build.pk if build else 0}:{ref.key}"
    names = cache.get(key)
    if names is None:
        with connections["misra"].cursor() as cur:
            cur.execute(f"SELECT {ref.feature_col}, COUNT(*) FROM {ref.table} GROUP BY {ref.feature_col}")
            names = sorted(((str(n), int(c)) for n, c in cur.fetchall() if n), key=lambda x: x[0].casefold())
        cache.set(key, names, 24 * 3600)
    return names


@endpoint("feature-names")
def feature_names(request):
    ref = reference_param(request)
    term = (request.GET.get("q") or "").strip().casefold()
    limit = int_param(request, "limit", 1000, 1, 100_000)
    names = [(n, c) for n, c in _feature_names(ref) if term in n.casefold()]
    page = names[:limit]
    if output_format(request) == "json":
        return json_response({"reference": ref.key, "count": len(names), "limit": limit,
                              "features": [{"name": n, "experiments": c} for n, c in page]},
                             request, f"misra_names_{ref.key}.json")
    return table_response(request, ["name", "experiments"], page, f"misra_names_{ref.key}")


@endpoint("filters")
def filters(request):
    catalog = catalog_or_error()
    sel = select_experiments(request, catalog, required=False)
    wanted = list_param(request, "field")
    limit = int_param(request, "limit", 50, 1, 10_000)
    panel = {d.key: d for d in FacetDefinition.objects.all()}
    keys = [k for k in CAT_KEYS if k != "study"] + NUM_KEYS
    bad = [k for k in wanted if k not in keys]
    if bad:
        raise ApiError(400, f"Unknown field(s) {', '.join(bad[:5])}. Fields: {', '.join(keys)}.")
    out = []
    for key in wanted or keys:
        label = panel[key].label if key in panel else FIELD_BY_KEY[key].label
        if key in NUM_KEYS:
            col = catalog.nums[key][sel.mask]
            col = col[~pd.isna(col)]
            out.append({"field": key, "label": label, "type": "range",
                        "min": float(col.min()) if len(col) else None, "max": float(col.max()) if len(col) else None,
                        "annotated": int(len(col))})
            continue
        values, n_values = catalog.facet_values(sel.evaluation, key, selected=sel.filters.get(key, []), limit=limit)
        out.append({"field": key, "label": label, "type": "values", "n_values": n_values,
                    "values": [{"value": v["value"] or None, "experiments": v["count"]} for v in values]})
    if output_format(request) == "json":
        return json_response({"experiments_selected": len(sel.ids), "fields": out}, request, "misra_filters.json")
    rows = ([f["field"], v["value"], v["experiments"]] for f in out if f["type"] == "values" for v in f["values"])
    return table_response(request, ["field", "value", "experiments"], rows, "misra_filters")


@endpoint("taxa")
def taxa(request):
    catalog = catalog_or_error()
    rank = RANKS[choice_param(request, "rank", list(RANKS), "kingdom")]
    sel = select_experiments(request, catalog, required=False)
    parents = TAX_RANKS[:TAX_RANKS.index(rank)]
    rows = catalog.taxon_table(sel.mask, rank, parents)
    term = (request.GET.get("term") or "").strip().casefold()
    if term:
        rows = [r for r in rows if term in r["value"].casefold() or any(term in x.casefold() for x in r["lineage"])]
    rows.sort(key=lambda r: (-r["experiments"], r["value"]))
    rank_name = next(k for k, v in RANKS.items() if v == rank)
    items = [{"name": r["value"] or None, "lineage": r["lineage"], "organisms": r["organisms"],
              "studies": r["studies"], "experiments": r["experiments"], "reads": r["reads"]} for r in rows]
    if output_format(request) == "json":
        return json_response({"rank": rank_name, "taxa": items}, request, f"misra_taxa_{rank_name}.json")
    columns = ["name", "lineage", "organisms", "studies", "experiments", "reads"]
    return table_response(request, columns, ([i["name"], " > ".join(i["lineage"])] + [i[c] for c in columns[2:]]
                                             for i in items), f"misra_taxa_{rank_name}")


@endpoint("studies")
def studies(request, accession=None):
    catalog = catalog_or_error()
    if accession:
        study = Study.objects.filter(pk=accession).first()
        if study is None:
            raise ApiError(404, f"There is no study {accession[:30]} in miSRA.")
        row = StudyRow.objects.filter(pk=accession).first()
        exps = catalog.exp_ids(catalog.mask_of_studies([accession]))
        pubmed = study.pubmed_ids if isinstance(study.pubmed_ids, list) else ([study.pubmed_ids] if study.pubmed_ids else [])
        return json_response({"study": study.id, "title": study.title, "abstract": study.abstract,
                              "pubmed_ids": [str(p) for p in pubmed], "organisms": row.organisms.split("; ") if row and row.organisms else [],
                              "groups": study.grouping_vars if isinstance(study.grouping_vars, dict) else {},
                              "processed": study.completed_at.isoformat() if study.completed_at else None,
                              "experiments": exps}, request, f"misra_study_{accession}.json")
    sel = select_experiments(request, catalog, required=False)
    offset = int_param(request, "offset", 0)
    limit = int_param(request, "limit", 100, 0, 10_000)
    rows, total = catalog.study_page(sel.mask, offset=offset, limit=limit)
    items = [{"study": r["study_id"], "title": r["title"], "organisms": r["organisms"],
              "experiments_matching": r["matching"], "experiments_total": r["total"]} for r in rows]
    if output_format(request) == "json":
        return json_response({"count": total, "offset": offset, "limit": limit, "studies": items}, request,
                             "misra_studies.json")
    columns = ["study", "title", "organisms", "experiments_matching", "experiments_total"]
    return table_response(request, columns, ([i[c] for c in columns] for i in items), "misra_studies")


@endpoint("selections")
def selections(request, slug):
    catalog = catalog_or_error()
    sel = Selection.objects.filter(slug=slug).first()
    if sel is None:
        raise ApiError(404, f"There is no saved selection “{slug[:30]}”.")
    ids = sel.id_list()
    present = {str(x) for x in catalog.exp_id[catalog.positions_of(ids)]}
    return json_response({"selection": sel.slug, "name": sel.name, "description": sel.description,
                          "saved": sel.created_at.isoformat(), "experiments": [e for e in ids if e in present],
                          "not_in_current_data": [e for e in ids if e not in present]}, request,
                         f"misra_selection_{sel.slug}.json")


@endpoint("")
def unknown(request, name):
    raise ApiError(404, f"There is no endpoint “{name[:40]}”. See the help page for the list.")


ENDPOINT_VIEWS = {v.api_name: v for v in [database, experiments, experiment_summary, rna_composition, read_lengths,
                                          expression_matrix, seed_expression, isomirs, trna_fragments,
                                          top_experiments, expression_summary, feature_names, filters, taxa,
                                          studies]}


def dispatch(request, name):
    view = ENDPOINT_VIEWS.get(name)
    if view is None:
        return unknown(request, name)
    return view(request)


# ---------------------------------------------------------------------------
# help page


@require_GET
def api_help(request):
    base = request.build_absolute_uri(reverse("misra:api_help")) + "v1/"
    sections = []
    for e in ENDPOINTS:
        path = e.name + ("/SRP103227" if e.name == "studies" else "")
        examples = [{"url": base + e.name + (f"?{urlencode(parse_qsl(q, keep_blank_values=True))}" if q else ""),
                     "label": e.name + (f"?{q}" if q else "")} for q in e.examples]
        if e.name == "studies":
            examples.append({"url": base + "studies/SRP103227", "label": "studies/SRP103227"})
        if e.name == "selections":
            examples.append({"url": None, "label": "selections/<id of a saved selection>"})
        sections.append({"e": e, "path": path, "examples": examples})
    return render(request, "misra/api.html", {
        "sections": sections, "base": base, "select_params": SELECT_PARAMS, "format_params": FORMAT_PARAMS,
        "formats": FORMATS, "matrix_limit": settings.MISRA_API_MATRIX_LIMIT,
        "detail_limit": settings.MISRA_API_DETAIL_LIMIT, "rate": settings.MISRA_API_RATE_PER_MIN,
    })

