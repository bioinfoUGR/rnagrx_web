"""The fields of the miSRA search index and how they are read from misradb2.

This is the single list of fields. Which of them appear in the filter panel, in
what order and with which label is configured separately (FacetDefinition,
editable in the Django admin).

Kinds:
  cat   categorical value: can be filtered (facet), counted and shown
  num   number: range filters, sorting
  text  free text: only searchable and shown/exported, too many distinct values to be a facet
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # "cat" | "num" | "text"


FIELDS: list[Field] = [
    Field("study", "Study", "cat"),
    Field("organism", "Organism", "cat"),
    Field("tax_kingdom", "Kingdom", "cat"),
    Field("tax_phylum", "Phylum", "cat"),
    Field("tax_class", "Class", "cat"),
    Field("tax_order", "Order", "cat"),
    Field("tax_family", "Family", "cat"),
    Field("tax_genus", "Genus", "cat"),
    Field("tax_species", "Species", "cat"),
    Field("sample_class", "Sample class", "cat"),
    Field("tissue", "Tissue", "cat"),
    Field("organism_part", "Organism part", "cat"),
    Field("primary_entity", "Primary entity", "cat"),
    Field("cell_type", "Cell type", "cat"),
    Field("cell_line", "Cell line", "cat"),
    Field("disease_state", "Disease state", "cat"),
    Field("cancer_sample", "Cancer sample", "cat"),
    Field("cancer_study", "From a cancer study", "cat"),
    Field("sex", "Sex", "cat"),
    Field("developmental_stage", "Developmental stage", "cat"),
    Field("protocol", "Library protocol (inferred)", "cat"),
    Field("rna_extraction", "RNA extraction", "cat"),
    Field("layout", "Read layout", "cat"),
    Field("availability", "Processing results", "cat"),
    Field("treatment", "Treatment / exposure", "text"),
    Field("age", "Age", "text"),
    Field("genotype", "Genotype", "text"),
    Field("read_count", "Reads after preprocessing", "num"),
    Field("raw_read_count", "Raw reads", "num"),
    Field("mirna_pct", "miRNA reads (%)", "num"),
    Field("trna_pct", "tRNA reads (%)", "num"),
    Field("rrna_pct", "rRNA reads (%)", "num"),
    Field("genome_pct", "Genome-mapped reads (%)", "num"),
    Field("bacteria_pct", "Bacteria-mapped reads (%)", "num"),
    Field("virus_pct", "Virus-mapped reads (%)", "num"),
]

FIELD_BY_KEY = {f.key: f for f in FIELDS}
CAT_KEYS = [f.key for f in FIELDS if f.kind == "cat"]
NUM_KEYS = [f.key for f in FIELDS if f.kind == "num"]
TEXT_KEYS = [f.key for f in FIELDS if f.kind == "text"]

# Taxonomy ranks from top to bottom, used by the "browse by taxonomy" page.
TAX_RANKS = ["tax_kingdom", "tax_phylum", "tax_class", "tax_order", "tax_family", "tax_genus", "organism"]

AVAILABILITY_LABELS = {
    "full": "miRNA profile + genome mapping",
    "genome_no_mirna": "genome mapping only",
    "mirna_no_genome": "miRNA profile only",
    "NA": "not available",
}

# Columns read from misradb2 for each experiment (see experiment_rows()).
EXPERIMENT_SQL = """
SELECT e.id, e.study_id, e.taxon_id, e.primary_entity, e.sample_class, e.tissue,
       e.read_count, e.raw_read_count, e.mirna_percent, e.trna_percent, e.rrna_percent,
       e.perc_genome_mapped, e.perc_bacteria_mapped, e.perc_virus_mapped,
       e.rna_extraction_method, e.protocol_guess, e.is_paired_end, e.from_cancer_study,
       e.created_at, a.inferred_metadata, av.state,
       t.scientific_name, t.kingdom_name, t.phylum_name, t.class_name, t.order_name,
       t.family_name, t.genus_name, t.species_name
FROM experiments e
LEFT JOIN experiment_analysis a ON a.experiment_id = e.id
LEFT JOIN experiments_available av ON av.exp_id = e.id
LEFT JOIN taxonomy t ON t.taxon_id = e.taxon_id
WHERE e.id > %s
ORDER BY e.id
LIMIT %s
"""

STUDY_SQL = "SELECT id, title, abstract, pubmed_ids, created_at FROM studies"


def clean(value) -> str | None:
    """Strip a categorical/text value; empty becomes None. No other normalisation:
    values are curated in the pipeline (see ~/www/misradb2_metadata_curation.md)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(v) for v in value if v not in (None, ""))
    text = " ".join(str(value).split())
    return text or None


def number(value) -> float | None:
    """misradb2 stores -1 for "not available"."""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    if x < 0 or math.isnan(x):
        return None
    return x


def _yes_no(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return "yes"
        if v in ("false", "no", "0"):
            return "no"
        return None
    return "yes" if value else "no"


def experiment_from_db(row: tuple, study_titles: dict[str, str]) -> dict:
    """Turn one row of EXPERIMENT_SQL into a flat dict with one entry per field."""
    (exp_id, study_id, taxon_id, primary_entity, sample_class, tissue,
     read_count, raw_read_count, mirna_pct, trna_pct, rrna_pct,
     genome_pct, bacteria_pct, virus_pct,
     rna_extraction, protocol, is_paired_end, from_cancer_study,
     created_at, inferred_json, state,
     scientific_name, kingdom, phylum, klass, order, family, genus, species) = row

    tags = {}
    other = {}
    if inferred_json:
        try:
            inferred = json.loads(inferred_json)
            tags = inferred.get("secondary_tags") or {}
            other = inferred.get("other_annotations") or {}
        except (ValueError, AttributeError):
            pass

    rec = {
        "exp_id": exp_id,
        "study": study_id,
        "taxon_id": clean(taxon_id),
        "organism": clean(scientific_name),
        "tax_kingdom": clean(kingdom),
        "tax_phylum": clean(phylum),
        "tax_class": clean(klass),
        "tax_order": clean(order),
        "tax_family": clean(family),
        "tax_genus": clean(genus),
        "tax_species": clean(species),
        "sample_class": clean(sample_class),
        "tissue": clean(tissue) or clean(tags.get("tissue")),
        "organism_part": clean(tags.get("organism_part")),
        "primary_entity": clean(primary_entity),
        "cell_type": clean(tags.get("cell_type")),
        "cell_line": clean(tags.get("cell_line")),
        "disease_state": clean(tags.get("disease_state")),
        "cancer_sample": _yes_no(tags.get("is_cancer")),
        "cancer_study": _yes_no(from_cancer_study),
        "sex": clean(tags.get("sex")),
        "developmental_stage": clean(tags.get("developmental_stage")),
        "protocol": clean(protocol),
        "rna_extraction": clean(rna_extraction) or clean(tags.get("rna_extraction_method")),
        "layout": None if is_paired_end is None else ("paired-end" if is_paired_end else "single-end"),
        "availability": AVAILABILITY_LABELS.get(state or "NA", clean(state)),
        "treatment": clean(tags.get("treatment_or_exposure")),
        "age": clean(tags.get("age")),
        "genotype": clean(tags.get("genotype")),
        "read_count": number(read_count),
        "raw_read_count": number(raw_read_count),
        "mirna_pct": number(mirna_pct),
        "trna_pct": number(trna_pct),
        "rrna_pct": number(rrna_pct),
        "genome_pct": number(genome_pct),
        "bacteria_pct": number(bacteria_pct),
        "virus_pct": number(virus_pct),
        "created_at": created_at,
    }
    words = [exp_id, study_id, study_titles.get(study_id) or ""]
    words += [rec[k] or "" for k in CAT_KEYS if k != "study"]
    words += [rec[k] or "" for k in TEXT_KEYS]
    words += [clean(v) or "" for v in other.values() if isinstance(v, (str, int, float))]
    rec["search_text"] = " ".join(w for w in words if w)
    return rec


def experiment_rows(cursor, study_titles: dict[str, str], batch: int = 2000):
    """Yield all experiments of misradb2 as flat dicts, reading in id-ordered batches
    (keyset paging keeps memory flat even with 300k experiments)."""
    last = ""
    while True:
        cursor.execute(EXPERIMENT_SQL, [last, batch])
        rows = cursor.fetchall()
        if not rows:
            return
        for row in rows:
            yield experiment_from_db(row, study_titles)
        last = rows[-1][0]


def study_rows(cursor) -> dict[str, dict]:
    cursor.execute(STUDY_SQL)
    out = {}
    for study_id, title, abstract, pubmed_json, created_at in cursor.fetchall():
        try:
            pubmed = json.loads(pubmed_json) if pubmed_json else []
        except ValueError:
            pubmed = []
        if not isinstance(pubmed, list):
            pubmed = [pubmed]
        out[study_id] = {
            "study_id": study_id,
            "title": clean(title) or "",
            "abstract": (abstract or "").strip(),
            "pubmed_ids": ",".join(str(p) for p in pubmed if p),
            "created_at": created_at,
        }
    return out
