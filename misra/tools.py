"""The miSRA tools: the direct entry point (paste accessions) and the cart/selection actions."""
from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from .forms import DownloadForm, MatrixForm


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

    @property
    def enabled(self) -> bool:
        return self.form_class is not None

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
         "Profile the experiments with sRNAbench against mature and hairpin sequences you provide."),
    Tool("libs", "Libs (longer references)", "Profiling against longer reference sequences",
         "Map the reads against references of 50 to a few hundred bases: counts and per-position coverage."),
    Tool("exact", "Exact matches (spike-ins)", "Exact-match profiling",
         "Count exact matches of the reads to short sequences (18–40 nt), e.g. spike-ins."),
]}
