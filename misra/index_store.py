"""Where the index arrays live and how each web worker loads the current build."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from .engine import Catalog
from .models import IndexBuild

log = logging.getLogger(__name__)

BUILD_ID_CACHE_KEY = "misra:build_id"
_lock = threading.Lock()
_catalog: Catalog | None = None


def array_path(build_id: int) -> Path:
    return Path(settings.MISRA_INDEX_DIR) / f"catalog_{build_id}.npz"


def current_build_id() -> int | None:
    build_id = cache.get(BUILD_ID_CACHE_KEY)
    if build_id is None:
        build_id = (IndexBuild.objects.filter(status="done").order_by("-id")
                    .values_list("id", flat=True).first()) or 0
        cache.set(BUILD_ID_CACHE_KEY, build_id, 30)
    return build_id or None


def current_build() -> IndexBuild | None:
    build_id = current_build_id()
    return IndexBuild.objects.filter(pk=build_id).first() if build_id else None


def get_catalog() -> Catalog | None:
    """The catalog of the newest finished build, loaded once per process."""
    global _catalog
    build_id = current_build_id()
    if build_id is None:
        return None
    if _catalog is not None and _catalog.build_id == build_id:
        return _catalog
    with _lock:
        if _catalog is None or _catalog.build_id != build_id:
            path = array_path(build_id)
            if not path.exists():
                log.error("index arrays %s missing", path)
                return _catalog
            _catalog = Catalog.load(path, build_id)
            log.info("loaded miSRA index build %s (%d experiments)", build_id, _catalog.n)
    return _catalog


def forget_build_id() -> None:
    cache.delete(BUILD_ID_CACHE_KEY)


def reset_for_tests() -> None:
    global _catalog
    _catalog = None
    forget_build_id()
