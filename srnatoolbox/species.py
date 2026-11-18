"""Species that sRNAbench can use, for the species search box.

- Species with a genome: SRNATOOLBOX_DB/species.json (accession -> taxon, names). All small RNA classes are
  profiled.
- Species with microRNAs only: the species lists of the four microRNA databases
  (SRNATOOLBOX_DB/mirna/*_Species_TaxonID.txt: taxon id, scientific name, short name). sRNAbench runs in
  microRNA-only mode with --taxon_id.
The index is rebuilt when one of these files changes.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

# microRNA database: (CLI value of --mirdb, label, species list file)
MIRNA_DBS = {
    "mg": ("MirGeneDB", "mgdb3_Species_TaxonID.txt"),
    "mb": ("miRBase", "miRBase_Species_TaxonID.txt"),
    "pm": ("PmiREN", "pMiren_Species_TaxonID.txt"),
    "mc": ("miRCarta", "miRCarta_Species_TaxonID.txt"),
}
MAX_HITS = 20


@dataclass
class Species:
    key: str  # accession for genome species, "taxon:<id>" otherwise
    taxon: str
    name: str
    common: str = ""
    accession: str = ""
    dbs: list[str] = field(default_factory=list)

    @property
    def has_genome(self) -> bool:
        return bool(self.accession)

    def label(self) -> str:
        return self.name + (f" ({self.common})" if self.common else "")

    def as_dict(self) -> dict:
        return {"key": self.key, "taxon": self.taxon, "name": self.name, "common": self.common,
                "accession": self.accession, "dbs": self.dbs, "genome": self.has_genome}


_lock = threading.Lock()
_cache: dict = {"stamp": None, "species": {}}


def _files() -> list[Path]:
    root = Path(settings.SRNATOOLBOX_DB or "/nonexistent")
    return [root / "species.json"] + [root / "mirna" / f for _, f in MIRNA_DBS.values()]


def _stamp() -> tuple:
    out = []
    for p in _files():
        try:
            out.append(p.stat().st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


def _build() -> dict[str, Species]:
    root = Path(settings.SRNATOOLBOX_DB or "/nonexistent")
    by_taxon: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for db, (_label, filename) in MIRNA_DBS.items():
        try:
            lines = (root / "mirna" / filename).read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip().isdigit():
                taxon = parts[0].strip()
                by_taxon.setdefault(taxon, [])
                if db not in by_taxon[taxon]:
                    by_taxon[taxon].append(db)
                names.setdefault(taxon, parts[1].strip())
    species: dict[str, Species] = {}
    genome_taxa = set()
    try:
        genomes = json.loads((root / "species.json").read_text())
    except (OSError, ValueError):
        genomes = {}
    for acc, info in genomes.items():
        taxon = str(info.get("taxon_id") or info.get("tax_id") or "")
        genome_taxa.add(taxon)
        species[acc] = Species(key=acc, taxon=taxon, name=info.get("organism_name", acc),
                               common=info.get("common_name", ""), accession=acc, dbs=by_taxon.get(taxon, []))
    for taxon, dbs in by_taxon.items():
        if taxon not in genome_taxa:
            species[f"taxon:{taxon}"] = Species(key=f"taxon:{taxon}", taxon=taxon, name=names[taxon], dbs=dbs)
    return species


def all_species() -> dict[str, Species]:
    stamp = _stamp()
    with _lock:
        if _cache["stamp"] != stamp:
            _cache["species"] = _build()
            _cache["stamp"] = stamp
        return _cache["species"]


def get(key: str) -> Species | None:
    return all_species().get(key)


def search(query: str, limit: int = MAX_HITS) -> list[Species]:
    q = " ".join(query.lower().split())
    if not q:
        return [s for s in all_species().values() if s.has_genome][:limit]
    hits = []
    for s in all_species().values():
        text = f"{s.name} {s.common}".lower()
        if q == s.taxon:
            rank = 0
        elif text.startswith(q) or any(w.startswith(q) for w in text.split()):
            rank = 1
        elif q in text or q in s.key.lower():
            rank = 2
        else:
            continue
        hits.append((rank, not s.has_genome, s.name, s))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits[:limit]]


def counts() -> tuple[int, int]:
    sp = all_species().values()
    n_genome = sum(1 for s in sp if s.has_genome)
    return n_genome, len(sp) - n_genome
