"""Free-text search over experiments (metadata + study title) and studies (title + abstract).

MariaDB: FULLTEXT indexes in boolean mode, every word required, prefix match ("liv" finds
"liver"). Words shorter than 3 characters are ignored (InnoDB's minimum token size).
Other databases (tests): case-insensitive substring match per word.
The result is a boolean mask over the catalog, cached per build and query.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np
from django.core.cache import cache
from django.db import connection

from .engine import Catalog
from .models import ExperimentRow, StudyRow

MIN_WORD = 3
WORD_RE = re.compile(r"[\w]+", re.UNICODE)
# InnoDB's default full-text stopwords: never indexed, so they must not be required words.
STOPWORDS = set("a about an are as at be by com de en for from how i in is it la of on or that the this "
                "to was what when where who will with und www".split())


def words(text: str) -> list[str]:
    return [w for w in WORD_RE.findall(text or "")
            if len(w) >= MIN_WORD and w.lower() not in STOPWORDS][:12]


def _boolean_query(ws: list[str]) -> str:
    return " ".join(f"+{w}*" for w in ws)


def _matching_ids(ws: list[str]) -> tuple[list[str], list[str]]:
    if connection.vendor == "mysql":
        q = _boolean_query(ws)
        exp_ids = list(ExperimentRow.objects.extra(
            where=["MATCH(search_text) AGAINST (%s IN BOOLEAN MODE)"], params=[q]).values_list("exp_id", flat=True))
        study_ids = list(StudyRow.objects.extra(
            where=["MATCH(title, abstract) AGAINST (%s IN BOOLEAN MODE)"], params=[q]).values_list("study_id", flat=True))
        return exp_ids, study_ids
    exps = ExperimentRow.objects.all()
    studies = StudyRow.objects.all()
    for w in ws:
        exps = exps.filter(search_text__icontains=w)
        studies = studies.filter(title__icontains=w) | studies.filter(abstract__icontains=w)
    return list(exps.values_list("exp_id", flat=True)), list(studies.values_list("study_id", flat=True))


def text_mask(catalog: Catalog, text: str) -> np.ndarray | None:
    """Mask of experiments matching the text (experiment metadata, or its study's title/abstract).
    None when the text has no searchable words."""
    ws = words(text)
    if not ws:
        return None
    key = "misra:text:%s:%s" % (catalog.build_id, hashlib.sha1(" ".join(ws).lower().encode()).hexdigest())
    cached = cache.get(key)
    if cached is not None:
        positions = np.frombuffer(cached, dtype=np.int64)
    else:
        exp_ids, study_ids = _matching_ids(ws)
        mask = catalog.mask_of_studies(study_ids)
        if exp_ids:
            mask[catalog.positions_of(exp_ids)] = True
        positions = np.nonzero(mask)[0].astype(np.int64)
        cache.set(key, positions.tobytes(), 600)
    return catalog.mask_of_positions(positions)
