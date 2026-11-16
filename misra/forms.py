import re
from collections import Counter

from django import forms
from django.conf import settings

from .runners import MATRIX_TABLES, MATRIX_VALUES

IUPAC = set("ACGTUNRYKMSWBDHV")
NAME_RE = re.compile(r"^[A-Za-z0-9_.:|+\-*/()\[\]]+$")


class Fasta:
    """A validated FASTA upload: (name, sequence) pairs, sequences upper case with U -> T."""

    def __init__(self, records):
        self.records = records

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
    max_bytes = settings.MISRA_UPLOAD_MAX_MB * 1024 * 1024
    if upload.size > max_bytes:
        raise forms.ValidationError(f"{label}: the file is larger than {settings.MISRA_UPLOAD_MAX_MB} MB.")
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


class ExperimentsForm(forms.Form):
    """Which experiments a job uses: pasted accessions, or the cart / a saved selection."""

    source = forms.CharField(required=False, widget=forms.HiddenInput)
    accessions = forms.CharField(
        required=False,
        label="Experiment or study accessions",
        help_text="SRX/ERX/DRX experiments and SRP/ERP/DRP studies (all their experiments), "
                  "separated by commas, spaces or new lines.",
        widget=forms.Textarea(attrs={"rows": 5, "class": "form-control font-monospace",
                                     "placeholder": "SRX2716515, SRX2716516\nSRP103227"}),
    )


class MatrixForm(ExperimentsForm):
    db = forms.ChoiceField(label="miRNA reference", choices=list(MATRIX_TABLES.items()), initial="mgdb",
                           widget=forms.Select(attrs={"class": "form-select"}))
    value = forms.ChoiceField(label="Value", choices=list(MATRIX_VALUES.items()), initial="rc",
                              widget=forms.Select(attrs={"class": "form-select"}))


SEPARATORS = [("#", "sRNAbench: #"), ("_x", "miRDeep: _x"), ("-", "FASTX-Toolkit: -"), ("_", "underscore: _"),
              (":", "colon: :")]


class DownloadForm(ExperimentsForm):
    sep = forms.ChoiceField(label="Separator between read id and count in the FASTA headers", choices=SEPARATORS,
                            initial="#", widget=forms.Select(attrs={"class": "form-select"}))
    outformat = forms.ChoiceField(label="Archive format", choices=[("zip", "ZIP"), ("tar.gz", "tar.gz")],
                                  initial="zip", widget=forms.Select(attrs={"class": "form-select"}))
    min_rc = forms.IntegerField(label="Minimum read count", min_value=0, initial=0, required=False,
                                widget=forms.NumberInput(attrs={"class": "form-control"}))
    min_len = forms.IntegerField(label="Minimum read length", min_value=0, initial=18, required=False,
                                 widget=forms.NumberInput(attrs={"class": "form-control"}))
    max_len = forms.IntegerField(label="Maximum read length", min_value=0, initial=26, required=False,
                                 help_text="Empty or 0: no maximum.",
                                 widget=forms.NumberInput(attrs={"class": "form-control"}))

    def clean(self):
        data = super().clean()
        lo, hi = data.get("min_len"), data.get("max_len")
        if lo and hi and hi < lo:
            self.add_error("max_len", "The maximum length is smaller than the minimum.")
        return data


def _int(label, initial, min_value=0, max_value=None, help_text=""):
    return forms.IntegerField(label=label, initial=initial, min_value=min_value, max_value=max_value, required=False,
                              help_text=help_text, widget=forms.NumberInput(attrs={"class": "form-control"}))


class BenchForm(ExperimentsForm):
    """Options shared by the sRNAbench tools."""

    min_rc = _int("Minimum read count", 1, min_value=1)
    min_len = _int("Minimum read length", 18, min_value=10, help_text="Empty: sRNAbench default (15).")
    max_len = _int("Maximum read length", None, min_value=10, help_text="Empty: no maximum.")

    def clean(self):
        data = super().clean()
        lo, hi = data.get("min_len"), data.get("max_len")
        if lo and hi and hi < lo:
            self.add_error("max_len", "The maximum length is smaller than the minimum.")
        return data


class MirnaForm(BenchForm):
    mature = FastaField(label="Mature miRNA sequences (FASTA)")
    hairpin = FastaField(label="Hairpin / precursor sequences (FASTA)")
    mm = _int("Allowed mismatches", 0, max_value=2)

    field_order = ["source", "accessions", "mature", "hairpin", "mm", "min_rc", "min_len", "max_len"]


class LibsForm(BenchForm):
    libs = FastaField(label="Reference sequences (FASTA)",
                      help_text="Typically 50 to a few hundred nucleotides, e.g. snoRNAs, piRNA clusters, transcripts.")
    mm = _int("Allowed mismatches", 0, max_value=2)

    field_order = ["source", "accessions", "libs", "mm", "min_rc", "min_len", "max_len"]


class ExactForm(BenchForm):
    spikes = FastaField(label="Spike-in or other short sequences (FASTA)",
                        help_text="Short sequences (typically 18–40 nt); only exact matches are counted.")

    field_order = ["source", "accessions", "spikes", "min_rc", "min_len", "max_len"]
