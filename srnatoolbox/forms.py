"""Form of the sRNAbench tool: settings for a selection of samples of a dataset."""
from __future__ import annotations

import re

from django import forms

from jobs.fasta import FastaField

from . import species as species_index

# --protocol values of sRNAbench, as offered on the form (CLI aliases left out)
PROTOCOLS = [
    ("guess", "Detect from the reads (recommended)"),
    ("Illumina", "Illumina TruSeq small RNA"),
    ("Illumina2", "Illumina, older adapter (v1.5)"),
    ("NextFlex", "NEXTflex (4 random nt on both ends)"),
    ("NextFlex4", "NEXTflex v4"),
    ("Qiaseq", "QIAseq miRNA (UMIs)"),
    ("NEB", "NEBNext small RNA"),
    ("NEBR4", "NEBNext with 4 random nt (NEBR4)"),
    ("NEBLB", "NEBNext low-bias (NEBLB)"),
    ("BGI", "BGI / MGI"),
    ("VAHTS", "VAHTS small RNA"),
    ("D", "D-Plex (Diagenode)"),
    ("trimmed", "Reads are already adapter-trimmed"),
    ("custom", "Other: give the 3' adapter below"),
]
PROTOCOL_LABELS = dict(PROTOCOLS)
MIRNA_REFS = [
    ("mg", "MirGeneDB"),
    ("mb", "miRBase"),
    ("pm", "PmiREN"),
    ("mc", "miRCarta"),
    ("own", "Your own mature + hairpin FASTA"),
]
MIRNA_LABELS = dict(MIRNA_REFS)
ADAPTER_RE = re.compile(r"^[ACGTN]{6,60}$")
# parameters of earlier jobs that can be copied into a new form ("start from the settings of a job")
COPYABLE = ["species", "mirdb", "protocol", "adapter", "random_5p", "random_3p", "min_len", "max_len",
            "min_rc", "mm", "adapter_mm", "min_adapter_length", "low_complexity", "seed_expression"]


def _num(label, initial, min_value, max_value, required=True):
    return forms.IntegerField(label=label, initial=initial, min_value=min_value, max_value=max_value,
                              required=required, widget=forms.NumberInput(attrs={"class": "form-control"}))


class SrnabenchForm(forms.Form):
    species = forms.CharField(widget=forms.HiddenInput, error_messages={
        "required": "Please choose a species."})
    mirdb = forms.ChoiceField(label="microRNA reference", choices=MIRNA_REFS, initial="mg",
                              widget=forms.RadioSelect)
    mature = FastaField(label="Mature microRNAs (FASTA)", required=False)
    hairpin = FastaField(label="Hairpins / precursors (FASTA)", required=False)
    libs = FastaField(label="Additional reference sequences (FASTA)", required=False,
                      help_text="e.g. piRNA clusters or transcripts; reads are counted per sequence")
    spikes = FastaField(label="Spike-ins (FASTA)", required=False,
                        help_text="short sequences counted by exact match")

    protocol = forms.ChoiceField(label="Library preparation protocol", choices=PROTOCOLS, initial="guess",
                                 widget=forms.Select(attrs={"class": "form-select"}))
    adapter = forms.CharField(label="3' adapter", required=False, max_length=60,
                              widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "TGGAATTCTCGG…"}))
    random_5p = _num("Random nt at the 5' end", 0, 0, 20)
    random_3p = _num("Random nt at the 3' end", 0, 0, 20)
    min_len = _num("Min. read length", 15, 10, 100)
    max_len = _num("Max. read length", None, 15, 1000, required=False)
    min_rc = _num("Min. read count", 1, 1, 1000)
    mm = forms.TypedChoiceField(label="Mismatches in mapping", choices=[(0, "0"), (1, "1"), (2, "2")], coerce=int,
                                initial=1, widget=forms.Select(attrs={"class": "form-select"}))
    # advanced
    adapter_mm = _num("Mismatches in the adapter", 1, 0, 3)
    min_adapter_length = _num("Min. adapter length found", 10, 4, 30)
    low_complexity = forms.BooleanField(label="Remove low-complexity reads", required=False, initial=True)
    seed_expression = forms.BooleanField(label="Also count expression per seed sequence", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("low_complexity", "seed_expression"):
            self.fields[name].widget.attrs["class"] = "form-check-input"

    def clean_adapter(self):
        return self.cleaned_data["adapter"].strip().upper().replace("U", "T")

    def clean(self):
        data = super().clean()
        sp = species_index.get(data.get("species", ""))
        if data.get("species") and sp is None:
            self.add_error("species", "Unknown species; please choose one from the list.")
        data["species_obj"] = sp
        ref = data.get("mirdb")
        if ref == "own":
            for name in ("mature", "hairpin"):
                if not data.get(name):
                    self.add_error(name, "Needed for your own microRNA reference.")
        elif sp is not None and ref and ref not in sp.dbs:
            self.add_error("mirdb", f"{MIRNA_LABELS[ref]} has no microRNAs of {sp.name}. "
                                    f"Choose another reference.")
        if sp is not None and not sp.has_genome and ref == "own":
            self.add_error("mirdb", "For species without genome, use one of the microRNA databases.")
        if data.get("protocol") == "custom" and not ADAPTER_RE.match(data.get("adapter", "")):
            self.add_error("adapter", "Give the 3' adapter sequence (6 to 60 of A, C, G, T, N).")
        if data.get("max_len") and data.get("min_len") and data["max_len"] < data["min_len"]:
            self.add_error("max_len", "Must not be smaller than the minimum length.")
        return data

    def params(self) -> dict:
        """The settings as stored in Job.params (JSON) and shown on the job page."""
        d = self.cleaned_data
        sp = d["species_obj"]
        p = {k: d.get(k) for k in COPYABLE if k in d}
        p.update({"species": sp.key, "species_name": sp.label(), "taxon": sp.taxon, "accession": sp.accession,
                  "genome": sp.has_genome})
        if d["protocol"] != "custom":
            p.update(adapter="", random_5p=0, random_3p=0)
        for name in ("mature", "hairpin", "libs", "spikes"):
            if d.get(name):
                p[f"{name}_file"] = d[name].describe(d[name].filename)
        return p


# ---------------------------------------------------------------------------
# sRNAde: differential expression between groups of samples of one finished sRNAbench job

LIBRARY_TYPES = [
    ("mirna", "microRNAs"),
    ("trna", "tRNAs"),
    ("libs", "Other RNA classes (genome annotation, own reference sequences)"),
]
LIBRARY_LABELS = dict(LIBRARY_TYPES)
DE_METHODS = [
    ("all", "DESeq2, edgeR and limma (consensus)"),
    ("DESeq2", "DESeq2"),
    ("edgeR", "edgeR"),
    ("limma", "limma-voom"),
]
DE_METHOD_LABELS = dict(DE_METHODS)
COUNT_TYPES = [
    ("rcadj", "Multi-mapping reads shared out between their references"),
    ("rcsa", "Multi-mapping reads assigned to the most expressed reference (slower)"),
    ("all", "Both, as separate analyses"),
]
COUNT_LABELS = dict(COUNT_TYPES)
FILTER_MODES = [
    ("min_per_group", "All samples of at least one group reach the threshold"),
    ("simple_mean", "The mean over all samples reaches the threshold"),
    ("fraction", "Most samples are above zero"),
]
FILTER_LABELS = dict(FILTER_MODES)


def _float(label, initial, min_value, max_value, step):
    return forms.FloatField(label=label, initial=initial, min_value=min_value, max_value=max_value,
                            widget=forms.NumberInput(attrs={"class": "form-control", "step": step}))


class SrnadeForm(forms.Form):
    """Samples and settings of an sRNAde run. `parent` (srnatoolbox.de.Parent) gives the samples of the
    sRNAbench job, their annotations and the RNA types it profiled."""

    column = forms.ChoiceField(label="Compare by", widget=forms.Select(attrs={"class": "form-select"}))
    groups = forms.MultipleChoiceField(label="Groups", widget=forms.CheckboxSelectMultiple,
                                       error_messages={"required": "Select at least two groups."})
    samples = forms.MultipleChoiceField(error_messages={"required": "Select the samples to compare."})
    library_type = forms.ChoiceField(label="RNA type", choices=LIBRARY_TYPES, initial="mirna",
                                     widget=forms.RadioSelect)
    method = forms.ChoiceField(label="Method", choices=DE_METHODS, initial="all",
                               widget=forms.Select(attrs={"class": "form-select"}))
    fdr = _float("FDR below", 0.05, 0.0001, 0.5, "0.01")
    log2fc = _float("|log2 fold change| at least", 1.0, 0, 10, "0.1")
    # advanced
    count_type = forms.ChoiceField(label="Reads mapping to several references", choices=COUNT_TYPES,
                                   initial="rcadj", widget=forms.Select(attrs={"class": "form-select"}))
    filter_mode = forms.ChoiceField(label="Expression filter", choices=FILTER_MODES, initial="min_per_group",
                                    widget=forms.Select(attrs={"class": "form-select"}))
    min_rc = _float("Min. read count", 1, 0, 10000, "1")
    min_rpm = _float("Min. RPM", 1, 0, 10000, "0.1")

    def __init__(self, *args, parent, column: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.parent = parent
        self.fields["column"].choices = [(c, c) for c in parent.columns]
        col = self.data.get("column") if self.is_bound else (column or self.initial.get("column"))
        if col not in parent.columns:
            col = parent.columns[0] if parent.columns else ""
        self.column = col
        self.fields["column"].initial = col
        self.fields["groups"].choices = [(g, g) for g, _ in parent.groups(col)]
        self.fields["samples"].choices = [(s["name"], s["name"]) for s in parent.samples]
        self.fields["library_type"].choices = [c for c in LIBRARY_TYPES if c[0] in parent.library_types]

    def clean(self):
        data = super().clean()
        col = data.get("column")
        groups = set(data.get("groups") or [])
        chosen = set(data.get("samples") or [])
        if not col:
            return data
        kept = [(s["name"], s["annotations"].get(col, "")) for s in self.parent.samples
                if s["name"] in chosen and s["annotations"].get(col, "") in groups]
        counts: dict[str, int] = {}
        for _, g in kept:
            counts[g] = counts.get(g, 0) + 1
        if len(counts) < 2:
            raise forms.ValidationError("Select samples of at least two groups.")
        from django.conf import settings
        if len(kept) > settings.SRNABENCH_MAX_SAMPLES:
            raise forms.ValidationError(f"Up to {settings.SRNABENCH_MAX_SAMPLES} samples per comparison.")
        data["kept"] = kept
        data["counts"] = counts
        return data

    def params(self) -> dict:
        d = self.cleaned_data
        return {"column": d["column"], "groups": sorted(d["counts"]), "library_type": d["library_type"],
                "method": d["method"], "fdr": d["fdr"], "log2fc": d["log2fc"], "count_type": d["count_type"],
                "filter_mode": d["filter_mode"], "min_rc": d["min_rc"], "min_rpm": d["min_rpm"]}
