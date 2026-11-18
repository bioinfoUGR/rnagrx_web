"""Sample sheets of a dataset: download as TSV, CSV or Excel, upload to rename samples, group files (lanes)
and set annotation columns.

Format: first columns `file` and `name`, then any annotation columns. Several files of one sample go in one
`file` cell separated by ';'. Applying a sheet replaces all samples of the dataset; ready files the sheet does not
list keep a sample of their own, named after the file.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import os

from django.db import transaction

from .datasets import (DatasetError, clean_value, sample_name_from_file, sample_rows, unique_sample_name,
                       validate_column, validate_sample_name)
from .models import DataFile, Dataset, Sample

MAX_ROWS = 10000
MAX_COLUMNS = 50
MAX_BYTES = 5 * 1024 * 1024
PENDING = "sheet_pending.json"
FORMATS = {"tsv": "text/tab-separated-values", "csv": "text/csv",
           "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class SheetError(ValueError):
    """The sheet cannot be used; the message is meant for the user."""


# ---------------------------------------------------------------------------
# download

def table(ds: Dataset) -> list[list[str]]:
    rows = [["file", "name"] + ds.columns]
    for r in sample_rows(ds):
        rows.append(["; ".join(f.name for f in r["files"]), r["sample"].name] + r["values"])
    return rows


def _safe_cell(value: str) -> str:
    # spreadsheet programs run cells starting with these as formulas
    return "'" + value if value[:1] in ("=", "+", "-", "@") and not value[1:2].isdigit() else value


def export(ds: Dataset, fmt: str) -> bytes:
    rows = [[_safe_cell(c) for c in row] for row in table(ds)]
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "samples"
        for row in rows:
            ws.append(row)
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    out = io.StringIO()
    csv.writer(out, delimiter="\t" if fmt == "tsv" else ",", lineterminator="\n").writerows(rows)
    return out.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# upload

def _cell(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        value = value.isoformat()
    return clean_value(value)


def parse(data: bytes, filename: str) -> list[list[str]]:
    """Rows of cells of a TSV, CSV or Excel file."""
    if len(data) > MAX_BYTES:
        raise SheetError("The sample sheet is larger than 5 MB.")
    if data[:4] == b"PK\x03\x04":
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:
            raise SheetError("The Excel file cannot be read. Save it as .xlsx, or as TSV or CSV.")
        rows = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            rows.append([_cell(v) for v in row])
            if len(rows) > MAX_ROWS + 1:
                break
        wb.close()
    else:
        if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise SheetError("Old Excel files (.xls) are not supported. Save the sheet as .xlsx, TSV or CSV.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        first = text.split("\n", 1)[0]
        delimiter = max(("\t", ",", ";"), key=first.count)
        rows = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = [r for r in rows if any(c for c in r)]
    while rows and rows[0] and not rows[0][-1]:
        rows[0].pop()
    if not rows:
        raise SheetError("The sample sheet is empty.")
    if len(rows) > MAX_ROWS + 1:
        raise SheetError(f"The sample sheet has more than {MAX_ROWS} rows.")
    return rows


def plan(ds: Dataset, rows: list[list[str]]) -> dict:
    """Check a parsed sheet against the dataset: {columns, samples, errors, unlisted, n_rows}."""
    header = [h.strip() for h in rows[0]]
    if len(header) < 2 or header[0].lower() != "file" or header[1].lower() != "name":
        raise SheetError("The first two columns must be called “file” and “name”. Download the template from "
                         "this page to get the right format.")
    columns = []
    for h in header[2:]:
        try:
            columns.append(validate_column(h, columns))
        except DatasetError as exc:
            raise SheetError(f"Column “{h}”: {exc}")
    if len(columns) > MAX_COLUMNS:
        raise SheetError(f"At most {MAX_COLUMNS} annotation columns are possible.")

    files = list(ds.files.filter(status=DataFile.READY))
    by_name = {f.name: f for f in files}
    by_tag = {f.tag: f for f in files}
    by_lower = {}
    for f in files:
        by_lower.setdefault(f.name.lower(), []).append(f)

    def find(cell: str):
        name = cell.strip()
        if name in by_name:
            return by_name[name]
        base = os.path.basename(name.replace("\\", "/"))
        if base in by_name:
            return by_name[base]
        same = by_lower.get(base.lower(), [])
        return same[0] if len(same) == 1 else None

    samples, errors, used, names = [], [], {}, set()
    for i, row in enumerate(rows[1:], start=2):
        row = row + [""] * (len(header) - len(row))
        if any(row[len(header):]):
            errors.append((i, "This row has more cells than the header."))
            continue
        cells = [c for c in (x.strip() for x in row[0].split(";")) if c]
        if not cells:
            errors.append((i, "The file column is empty."))
            continue
        tags, bad = [], False
        for c in cells:
            f = find(c)
            if f is None:
                errors.append((i, f"“{c}” is not a file of this dataset (or not ready yet)."))
                bad = True
            elif f.tag in used:
                errors.append((i, f"“{f.name}” is already used in row {used[f.tag]}."))
                bad = True
            else:
                used[f.tag] = i
                tags.append(f.tag)
        name = row[1].strip()
        if not name:
            if not tags:
                continue
            name = sample_name_from_file(by_tag[tags[0]].name)[0]
        try:
            name = validate_sample_name(name)
            if name in names:
                raise DatasetError(f"The name “{name}” is used twice.")
            names.add(name)
        except DatasetError as exc:
            errors.append((i, str(exc)))
            bad = True
        if not bad:
            samples.append({"name": name, "files": tags,
                            "annotations": {c: v for c, v in zip(columns, row[2:]) if v}})
    unlisted = [f for f in files if f.tag not in used]
    return {"columns": columns, "samples": samples, "errors": errors[:200], "n_errors": len(errors),
            "unlisted": [f.name for f in unlisted], "unlisted_tags": [f.tag for f in unlisted],
            "n_rows": len(rows) - 1}


def save_pending(ds: Dataset, p: dict) -> None:
    ds.directory().mkdir(parents=True, exist_ok=True)
    (ds.directory() / PENDING).write_text(json.dumps(p))


def load_pending(ds: Dataset) -> dict | None:
    try:
        return json.loads((ds.directory() / PENDING).read_text())
    except (OSError, ValueError):
        return None


def drop_pending(ds: Dataset) -> None:
    (ds.directory() / PENDING).unlink(missing_ok=True)


def apply(ds: Dataset, p: dict) -> int:
    """Replace the samples and columns of the dataset by a checked plan without errors."""
    if p.get("n_errors"):
        raise SheetError("The sample sheet has errors.")
    ready = set(ds.files.filter(status=DataFile.READY).values_list("tag", flat=True))
    if any(t not in ready for s in p["samples"] for t in s["files"]):
        raise SheetError("Files of the dataset changed since the sheet was checked; please upload it again.")
    names_by_tag = dict(ds.files.filter(tag__in=ready).values_list("tag", "name"))
    with transaction.atomic():
        Dataset.objects.select_for_update().get(pk=ds.pk)
        ds.samples.all().delete()
        taken = set()
        new = []
        for pos, s in enumerate(p["samples"], start=1):
            taken.add(s["name"])
            new.append(Sample(dataset=ds, position=pos, name=s["name"], files=s["files"],
                              annotations=s["annotations"]))
        listed = {t for s in p["samples"] for t in s["files"]}
        pos = len(new)
        for tag in sorted(ready - listed, key=lambda t: names_by_tag[t]):
            pos += 1
            name = unique_sample_name(ds, sample_name_from_file(names_by_tag[tag])[0], taken)
            taken.add(name)
            new.append(Sample(dataset=ds, position=pos, name=name, files=[tag]))
        Sample.objects.bulk_create(new)
        ds.columns = p["columns"]
        ds.save(update_fields=["columns"])
    drop_pending(ds)
    return len(new)
