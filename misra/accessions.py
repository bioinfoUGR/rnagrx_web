"""Turning a pasted list of SRA accessions into the experiments of the miSRA index."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .engine import Catalog

SPLIT_RE = re.compile(r"[\s,;]+")
EXPERIMENT_RE = re.compile(r"^[SED]RX\d+$")
STUDY_RE = re.compile(r"^[SED]RP\d+$")
MAX_TOKENS = 100_000


@dataclass
class Resolved:
    exp_ids: list[str] = field(default_factory=list)  # found, sorted, unique
    n_from_experiments: int = 0
    n_from_studies: int = 0
    unknown_experiments: list[str] = field(default_factory=list)  # well-formed but not in miSRA
    unknown_studies: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)  # not an SRX/ERX/DRX or SRP/ERP/DRP accession

    @property
    def problems(self) -> int:
        return len(self.unknown_experiments) + len(self.unknown_studies) + len(self.invalid)


def tokens(text: str) -> list[str]:
    return [t.upper() for t in SPLIT_RE.split(text or "") if t][:MAX_TOKENS]


def resolve(text: str, catalog: Catalog) -> Resolved:
    exps, studies, invalid = [], [], []
    for t in dict.fromkeys(tokens(text)):
        if EXPERIMENT_RE.match(t):
            exps.append(t)
        elif STUDY_RE.match(t):
            studies.append(t)
        else:
            invalid.append(t)
    found = set()
    res = Resolved(invalid=invalid)
    if exps:
        pos = catalog.positions_of(exps)
        known = {str(x) for x in catalog.exp_id[pos]}
        res.unknown_experiments = [e for e in exps if e not in known]
        res.n_from_experiments = len(known)
        found |= known
    if studies:
        known_studies = [s for s in studies if s in catalog.vocab_index["study"]]
        res.unknown_studies = [s for s in studies if s not in catalog.vocab_index["study"]]
        from_studies = set(catalog.exp_ids(catalog.mask_of_studies(known_studies)))
        res.n_from_studies = len(from_studies - found)
        found |= from_studies
    res.exp_ids = sorted(found)
    return res
