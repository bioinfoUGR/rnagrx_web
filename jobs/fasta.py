"""Validation of small FASTA uploads (reference sequences, spike-ins) in tool forms."""
import re
from collections import Counter

from django import forms
from django.conf import settings

IUPAC = set("ACGTUNRYKMSWBDHV")
NAME_RE = re.compile(r"^[A-Za-z0-9_.:|+\-*/()\[\]]+$")


class Fasta:
    """A validated FASTA upload: (name, sequence) pairs, sequences upper case with U -> T."""

    def __init__(self, records):
        self.records = records
        self.filename = ""

    @property
    def n(self):
        return len(self.records)

    @property
    def total_length(self):
        return sum(len(s) for _, s in self.records)

    def text(self) -> str:
        return "".join(f">{name}\n{seq}\n" for name, seq in self.records)

    def describe(self, filename: str) -> str:
        return f"{filename} ({self.n:,} sequences, {self.total_length:,} nt)"


def parse_fasta(upload, label: str) -> Fasta:
    max_mb = settings.FASTA_UPLOAD_MAX_MB
    if upload.size > max_mb * 1024 * 1024:
        raise forms.ValidationError(f"{label}: the file is larger than {max_mb} MB.")
    try:
        text = upload.read().decode("utf-8")
    except UnicodeDecodeError:
        raise forms.ValidationError(f"{label}: this is not a plain-text FASTA file.")
    records, name, seq = [], None, []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if name is not None:
                records.append((name, "".join(seq)))
            name = line[1:].split()[0] if line[1:].split() else ""
            if not name or not NAME_RE.match(name):
                raise forms.ValidationError(f"{label}, line {lineno}: invalid sequence name “{line[:60]}”.")
            seq = []
        else:
            if name is None:
                raise forms.ValidationError(f"{label}: the file must start with a “>” header line.")
            bad = set(line.upper()) - IUPAC
            if bad:
                raise forms.ValidationError(
                    f"{label}, line {lineno}: invalid character(s) {' '.join(sorted(bad))} in a sequence.")
            seq.append(line.upper().replace("U", "T"))
    if name is not None:
        records.append((name, "".join(seq)))
    if not records:
        raise forms.ValidationError(f"{label}: no sequences found.")
    empty = [n for n, s in records if not s]
    if empty:
        raise forms.ValidationError(f"{label}: sequence “{empty[0]}” is empty.")
    dup = [n for n, c in Counter(n for n, _ in records).items() if c > 1]
    if dup:
        raise forms.ValidationError(f"{label}: duplicate sequence name(s): {', '.join(sorted(dup)[:5])}.")
    return Fasta(records)


class FastaField(forms.FileField):
    widget = forms.ClearableFileInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", forms.FileInput(attrs={"class": "form-control", "accept": ".fa,.fasta,.fna,.txt"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        if upload is None:
            return None
        fasta = parse_fasta(upload, self.label)
        fasta.filename = upload.name[:100]
        return fasta
