"""Small synthetic miSRA catalogue for tests."""
from misra.catalog import CAT_KEYS, NUM_KEYS, TEXT_KEYS


def record(exp_id, study, **values):
    rec = {"exp_id": exp_id, "study": study, "taxon_id": None, "created_at": None}
    for k in CAT_KEYS:
        if k != "study":
            rec[k] = None
    for k in NUM_KEYS + TEXT_KEYS:
        rec[k] = None
    rec.update(values)
    rec["search_text"] = " ".join(str(v) for k, v in rec.items() if isinstance(v, str))
    return rec


def sample_records():
    return [
        record("SRX001", "SRP1", organism="Homo sapiens", tax_kingdom="Metazoa", tissue="blood", sex="male",
               sample_class="biofluid", read_count=5e6, mirna_pct=40.0),
        record("SRX002", "SRP1", organism="Homo sapiens", tax_kingdom="Metazoa", tissue="blood", sex="female",
               sample_class="biofluid", read_count=2e5, mirna_pct=10.0),
        record("SRX003", "SRP1", organism="Homo sapiens", tax_kingdom="Metazoa", tissue="brain", sex="male",
               sample_class="tissue_or_organ", read_count=8e6, mirna_pct=55.0),
        record("SRX004", "SRP2", organism="Mus musculus", tax_kingdom="Metazoa", tissue="liver",
               sample_class="tissue_or_organ", read_count=1e6, mirna_pct=30.0, disease_state="fibrosis"),
        record("SRX005", "SRP2", organism="Mus musculus", tax_kingdom="Metazoa", tissue="blood",
               sample_class="biofluid", read_count=None, mirna_pct=None),
        record("ERX006", "ERP3", organism="Zea mays", tax_kingdom="Viridiplantae", tissue=None,
               organism_part="leaf", sample_class="tissue_or_organ", read_count=3e6, mirna_pct=20.0,
               treatment="drought stress"),
    ]


def sample_studies():
    return {
        "SRP1": {"study_id": "SRP1", "title": "Human blood and brain", "abstract": "Plasma exosome profiling.",
                 "pubmed_ids": "", "created_at": None},
        "SRP2": {"study_id": "SRP2", "title": "Mouse liver fibrosis", "abstract": "", "pubmed_ids": "",
                 "created_at": None},
        "ERP3": {"study_id": "ERP3", "title": "Maize drought", "abstract": "Leaves under water stress.",
                 "pubmed_ids": "", "created_at": None},
    }
