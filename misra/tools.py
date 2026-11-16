"""The miSRA tools: the direct entry point (paste accessions) and the cart/selection actions."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from .forms import DownloadForm, ExactForm, LibsForm, MatrixForm, MirnaForm


@dataclass(frozen=True)
class Tool:
    key: str
    label: str  # menu entry
    title: str
    description: str
    form_class: type | None = None
    queue: str = "rnagrx_quick"
    limit_setting: str = "MISRA_LIMIT_MATRIX"
    params: tuple = field(default=())  # form fields stored in Job.params
    files: tuple = field(default=())  # FASTA upload fields, saved as input/<field>.fa
    blocked: str = ""  # reason the tool is temporarily unavailable

    @property
    def enabled(self) -> bool:
        return self.form_class is not None and not self.blocked

    @property
    def limit(self) -> int:
        return getattr(settings, self.limit_setting)


TOOLS = {t.key: t for t in [
    Tool("download", "Download reads", "Download preprocessed reads",
         "Adapter-trimmed and quality-filtered reads of the experiments, collapsed to unique sequences "
         "with their counts, one FASTA file per experiment in one archive.",
         DownloadForm, "rnagrx_quick", "MISRA_LIMIT_DOWNLOAD", ("sep", "outformat", "min_rc", "min_len", "max_len")),
    Tool("matrix", "Expression matrix", "miRNA expression matrix",
         "miRNA expression values of the experiments as one table (miRNAs × experiments), from the "
         "miSRA profiles against MirGeneDB, miRBase or PmiREN.",
         MatrixForm, "rnagrx_quick", "MISRA_LIMIT_MATRIX", ("db", "value")),
    Tool("mirna", "miRNA profiling", "miRNA profiling with your own annotation",
         "Profile the experiments with sRNAbench against mature and hairpin (precursor) miRNA sequences you "
         "provide, e.g. from MirGeneDB or miRBase, or your own predictions.",
         MirnaForm, "rnagrx_heavy", "MISRA_LIMIT_SRNABENCH", ("mm", "min_rc", "min_len", "max_len"),
         ("mature", "hairpin")),
    Tool("libs", "Libs (longer references)", "Profiling against longer reference sequences",
         "Map the reads with sRNAbench against references of 50 to a few hundred bases you provide: "
         "read counts per reference.",
         LibsForm, "rnagrx_heavy", "MISRA_LIMIT_SRNABENCH", ("mm", "min_rc", "min_len", "max_len"), ("libs",)),
    Tool("exact", "Exact matches (spike-ins)", "Exact-match profiling",
         "Count reads that match short sequences you provide (typically 18–40 nt, e.g. spike-ins) exactly.",
         ExactForm, "rnagrx_heavy", "MISRA_LIMIT_SRNABENCH", ("min_rc", "min_len", "max_len"), ("spikes",)),
]}
