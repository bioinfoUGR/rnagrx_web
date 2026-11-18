"""The miSRA tools: the direct entry point (paste accessions) and the cart/selection actions."""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from jobs.registry import Tool, register, tools_of

from .forms import DownloadForm, ExactForm, LibsForm, MatrixForm, MirnaForm


@dataclass(frozen=True, kw_only=True)
class MisraTool(Tool):
    app: str = "misra"
    limit_setting: str = "MISRA_LIMIT_MATRIX"  # max experiments per job

    @property
    def limit(self) -> int:
        return getattr(settings, self.limit_setting)


register(
    MisraTool(key="download", label="Download reads", title="Download preprocessed reads",
              description="Adapter-trimmed and quality-filtered reads of the experiments, collapsed to unique "
                          "sequences with their counts, one FASTA file per experiment in one archive.",
              runner="misra.runners.run_download", form_class=DownloadForm, queue="rnagrx_quick",
              limit_setting="MISRA_LIMIT_DOWNLOAD", params=("sep", "outformat", "min_rc", "min_len", "max_len")),
    MisraTool(key="matrix", label="Expression matrix", title="miRNA expression matrix",
              description="miRNA expression values of the experiments as one table (miRNAs × experiments), from the "
                          "miSRA profiles against MirGeneDB, miRBase or PmiREN.",
              runner="misra.runners.run_matrix", form_class=MatrixForm, queue="rnagrx_quick",
              limit_setting="MISRA_LIMIT_MATRIX", params=("db", "value")),
    MisraTool(key="mirna", label="miRNA profiling", title="miRNA profiling with your own annotation",
              description="Profile the experiments with sRNAbench against mature and hairpin (precursor) miRNA "
                          "sequences you provide, e.g. from MirGeneDB or miRBase, or your own predictions.",
              runner="misra.runners.run_srnabench", form_class=MirnaForm, queue="rnagrx_heavy",
              limit_setting="MISRA_LIMIT_SRNABENCH", params=("mm", "min_rc", "min_len", "max_len"),
              files=("mature", "hairpin")),
    MisraTool(key="libs", label="Libs (longer references)", title="Profiling against longer reference sequences",
              description="Map the reads with sRNAbench against references of 50 to a few hundred bases you "
                          "provide: read counts per reference.",
              runner="misra.runners.run_srnabench", form_class=LibsForm, queue="rnagrx_heavy",
              limit_setting="MISRA_LIMIT_SRNABENCH", params=("mm", "min_rc", "min_len", "max_len"), files=("libs",)),
    MisraTool(key="exact", label="Exact matches (spike-ins)", title="Exact-match profiling",
              description="Count reads that match short sequences you provide (typically 18–40 nt, e.g. spike-ins) "
                          "exactly.",
              runner="misra.runners.run_srnabench", form_class=ExactForm, queue="rnagrx_heavy",
              limit_setting="MISRA_LIMIT_SRNABENCH", params=("min_rc", "min_len", "max_len"), files=("spikes",)),
)

TOOLS: dict[str, MisraTool] = tools_of("misra")
