"""The browse state, kept entirely in the URL so every view can be bookmarked and shared.

  tab=experiments|studies
  q=<free text>
  f_<field>=<value>       repeated for OR within a field; different fields are ANDed;
                          an empty value means "not annotated"
  min_<field>, max_<field> number ranges
  sort=<name>, desc=1, page=<n>
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from .catalog import CAT_KEYS, FIELD_BY_KEY, NUM_KEYS
from .engine import SORTS, Query

TABS = ("experiments", "studies")
STUDY_SORTS = ("matching", "id")
MAX_VALUES_PER_FIELD = 500


def _float(value):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x else None  # drop NaN


@dataclass
class BrowseQuery:
    tab: str = "experiments"
    text: str = ""
    filters: dict[str, list[str]] = field(default_factory=dict)
    ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    sort: str = "id"
    desc: bool = False
    page: int = 1

    @classmethod
    def from_params(cls, params) -> "BrowseQuery":
        tab = params.get("tab")
        tab = tab if tab in TABS else "experiments"
        filters = {}
        for key in CAT_KEYS:
            values = params.getlist(f"f_{key}")
            if values:
                seen = []
                for v in values:
                    v = v.strip()
                    if v not in seen:
                        seen.append(v)
                filters[key] = seen[:MAX_VALUES_PER_FIELD]
        ranges = {}
        for key in NUM_KEYS:
            lo, hi = _float(params.get(f"min_{key}")), _float(params.get(f"max_{key}"))
            if lo is not None or hi is not None:
                ranges[key] = (lo, hi)
        sort = params.get("sort") or ("matching" if tab == "studies" else "id")
        valid_sorts = STUDY_SORTS if tab == "studies" else SORTS
        if sort not in valid_sorts:
            sort = "matching" if tab == "studies" else "id"
        try:
            page = max(1, int(params.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        return cls(
            tab=tab,
            text=(params.get("q") or "").strip()[:200],
            filters=filters,
            ranges=ranges,
            sort=sort,
            desc=params.get("desc") == "1",
            page=page,
        )

    # ---------- URL building ----------

    def filter_params(self) -> list[tuple[str, str]]:
        """Parameters that define *which* experiments match (no tab, sorting or page)."""
        items: list[tuple[str, str]] = []
        if self.text:
            items.append(("q", self.text))
        for key in CAT_KEYS:
            for v in self.filters.get(key, []):
                items.append((f"f_{key}", v))
        for key in NUM_KEYS:
            if key in self.ranges:
                lo, hi = self.ranges[key]
                if lo is not None:
                    items.append((f"min_{key}", _fmt(lo)))
                if hi is not None:
                    items.append((f"max_{key}", _fmt(hi)))
        return items

    def params(self, **override) -> list[tuple[str, str]]:
        tab = override.get("tab", self.tab)
        items = [("tab", tab)] if tab != "experiments" else []
        items += self.filter_params()
        sort = override.get("sort", self.sort if tab == self.tab else None)
        desc = override.get("desc", self.desc if tab == self.tab else False)
        default_sort = "matching" if tab == "studies" else "id"
        if sort and sort != default_sort:
            items.append(("sort", sort))
        if desc:
            items.append(("desc", "1"))
        page = override.get("page", 1)
        if page and page > 1:
            items.append(("page", str(page)))
        return items

    def urlencode(self, **override) -> str:
        return urlencode(self.params(**override))

    def without(self, key: str, value: str | None = None) -> str:
        """Query string with one filter value (or a whole range / the text) removed."""
        clone = BrowseQuery(self.tab, self.text, {k: list(v) for k, v in self.filters.items()},
                            dict(self.ranges), self.sort, self.desc, 1)
        if key == "q":
            clone.text = ""
        elif key in clone.ranges:
            del clone.ranges[key]
        elif key in clone.filters:
            clone.filters[key] = [v for v in clone.filters[key] if v != value]
            if not clone.filters[key]:
                del clone.filters[key]
        return clone.urlencode()

    def sort_link(self, sort: str) -> str:
        desc = not self.desc if sort == self.sort else sort in ("reads", "mirna", "genome", "matching")
        return self.urlencode(sort=sort, desc=desc)

    # ---------- engine ----------

    def engine_query(self) -> Query:
        return Query(filters=dict(self.filters), ranges=dict(self.ranges))

    def chips(self) -> list[dict]:
        chips = []
        if self.text:
            chips.append({"label": "Text", "value": self.text, "remove": self.without("q")})
        for key, values in self.filters.items():
            for v in values:
                chips.append({"label": FIELD_BY_KEY[key].label, "value": v or "not annotated",
                              "remove": self.without(key, v)})
        for key, (lo, hi) in self.ranges.items():
            if lo is not None and hi is not None:
                text = f"{_fmt(lo)} – {_fmt(hi)}"
            elif lo is not None:
                text = f"≥ {_fmt(lo)}"
            else:
                text = f"≤ {_fmt(hi)}"
            chips.append({"label": FIELD_BY_KEY[key].label, "value": text, "remove": self.without(key)})
        return chips

    def describe(self) -> str:
        return "; ".join(f"{c['label']}: {c['value']}" for c in self.chips()) or "all experiments"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"
