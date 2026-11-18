from django import forms

from jobs.fasta import Fasta, FastaField, parse_fasta  # noqa: F401  (re-exported)

from .runners import MATRIX_TABLES, MATRIX_VALUES


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
