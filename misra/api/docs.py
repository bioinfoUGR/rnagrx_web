"""Descriptions of the API endpoints: rendered on the help page and listed by the `database` endpoint."""
from dataclasses import dataclass, field

SELECT_PARAMS = [
    ("exp", "", "Experiment accessions (SRX/ERX/DRX), separated by commas."),
    ("study", "", "Study accessions (SRP/ERP/DRP): all their experiments."),
    ("selection", "", "The id of a selection saved on the website (the last part of its link)."),
    ("taxon", "", "NCBI taxon id or a taxon name at any rank (Homo sapiens, Mammalia, Viridiplantae)."),
    ("<field>=<value>", "", "Filters as on the browse page, e.g. tissue=blood&sex=female; repeat a field for OR "
                            "(tissue=blood&tissue=plasma). The fields are listed by the filters endpoint; "
                            "an empty value means “not annotated”. Filters copied from a browse link (f_tissue=) "
                            "work as well."),
    ("min_<field> / max_<field>", "", "Ranges, e.g. min_read_count=1000000, min_mirna_pct=20."),
    ("q", "", "Free text, as in the browse search box (all words must match)."),
]
FORMAT_PARAMS = [
    ("format", "json", "json, tsv or csv."),
    ("download", "", "If set (download=1), the result is sent as a file."),
]
REFERENCE_DOC = ("reference", "mirgenedb", "mirgenedb, mirbase, pmiren (miRNAs), trna-nuclear, trna-mito (tRNAs).")
VALUE_DOC = ("value", "rpm_total", "read_count, adjusted_read_count (multi-mapping reads split), rpm_library "
                                   "(per million reads assigned to the reference), rpm_total (per million reads in "
                                   "the analysis).")


@dataclass
class Endpoint:
    name: str
    title: str
    summary: str
    params: list = field(default_factory=list)
    examples: list = field(default_factory=list)
    returns: str = ""
    selects: str = ""  # "required" / "optional" / "": accepts the experiment selection parameters
    formats: bool = True


ENDPOINTS = [
    Endpoint("database", "Database", "Version (date of the current build), counts, references, values and endpoints.",
             examples=[""], returns="JSON.", formats=False),
    Endpoint("experiments", "Experiments", "Search experiments with the browse filters; returns accessions and metadata.",
             params=[("fields", "all", "Columns to return, comma-separated (e.g. fields=exp_id,organism,tissue)."),
                     ("limit", "100 (JSON)", "Number of experiments; at most 10,000 in JSON, unlimited in TSV/CSV."),
                     ("offset", "0", "Skip this many experiments (paging).")],
             examples=["organism=Homo sapiens&tissue=blood&min_read_count=5000000&limit=5",
                       "study=SRP103227&fields=exp_id,organism,tissue,read_count&format=tsv",
                       "q=exosome&sex=female&limit=10"],
             returns="JSON: count (all matches), experiments (one object per experiment). TSV/CSV: one row per "
                     "experiment.", selects="optional"),
    Endpoint("experiment-summary", "Experiment summary",
             "Metadata, read statistics (raw reads, preprocessing steps, reads in the analysis) and the percentage of "
             "reads that are miRNA, tRNA and rRNA or map to the genome, bacteria and viruses.",
             examples=["exp=SRX2716515", "exp=SRX2716515,SRX2716516&format=tsv"],
             returns="JSON: experiment -> {metadata, reads, preprocessing, percent}. TSV/CSV: one row per experiment.",
             selects="required"),
    Endpoint("rna-composition", "RNA composition", "Reads per RNA type (miRNA, tRNA, rRNA, snoRNA, Y RNA …) per experiment.",
             examples=["exp=SRX2716515", "exp=SRX2716515,SRX2716516&format=tsv"],
             returns="JSON: experiment -> list of {rna_type, unique_reads, unique_reads_percent, read_count, "
                     "read_count_percent}. TSV/CSV: one row per experiment and RNA type.", selects="required"),
    Endpoint("read-lengths", "Read lengths", "Read-length distribution per experiment.",
             examples=["exp=SRX2716515"],
             returns="JSON: experiment -> list of {length, unique_reads, unique_reads_percent, read_count, "
                     "read_count_percent, rpm}, only lengths with reads. TSV/CSV: one row per experiment and length.",
             selects="required"),
    Endpoint("expression-matrix", "Expression matrix",
             "miRNA or tRNA expression of the selected experiments as a matrix (features × experiments).",
             params=[REFERENCE_DOC, VALUE_DOC,
                     ("feature", "all", "Only these miRNAs/tRNAs (comma-separated).")],
             examples=["exp=SRX2716515,SRX2716516&value=read_count&format=tsv",
                       "study=SRP201970&feature=Mmu-Mir-21_5p,Mmu-Let-7-P1d_5p",
                       "exp=SRX2716515&reference=trna-nuclear&format=tsv"],
             returns="JSON: features, experiments and data (one list of values per feature, in the order of "
                     "experiments; pandas: pd.DataFrame(r['data'], index=r['features'], columns=r['experiments'])). "
                     "TSV/CSV: the matrix. Experiments without values are listed in experiments_without_values.",
             selects="required"),
    Endpoint("top-experiments", "Top experiments",
             "The experiments in which one miRNA or tRNA is most highly expressed, with their metadata.",
             params=[("feature", "required", "One miRNA or tRNA name (see feature-names)."), REFERENCE_DOC,
                     ("order_by", "rpm_total", "read_count, adjusted_read_count, rpm_library or rpm_total."),
                     ("limit", "20", "Number of experiments, at most 1,000.")],
             examples=["feature=Hsa-Mir-122_5p&limit=5", "feature=Hsa-Mir-21_5p&tissue=blood&order_by=read_count&limit=5"],
             returns="JSON: list of experiments with metadata and all four values. TSV/CSV: one row per experiment.",
             selects="optional"),
    Endpoint("expression-summary", "Expression summary",
             "For each miRNA/tRNA: in how many experiments it has reads, and its total, mean, median and maximum "
             "expression, over all experiments or a selection.",
             params=[("feature", "required", "miRNA/tRNA names, comma-separated (at most 100)."), REFERENCE_DOC,
                     VALUE_DOC],
             examples=["feature=Hsa-Mir-21_5p,Hsa-Mir-122_5p", "feature=Hsa-Mir-21_5p&organism=Homo sapiens&format=tsv"],
             returns="JSON: feature -> statistics. mean_all = sum / number of selected experiments (all of miSRA "
                     "if nothing is selected, so add e.g. organism= for a meaningful average); mean_with_reads and "
                     "median_with_reads only over experiments with reads. TSV/CSV: one row per feature.",
             selects="optional"),
    Endpoint("isomirs", "IsomiRs", "Read counts per isomiR class (canonical, 3′/5′ length variants, non-templated "
                                   "additions …) for each miRNA.",
             params=[("reference", "mirgenedb", "mirgenedb, mirbase or pmiren."),
                     ("feature", "all", "Only these miRNAs (comma-separated).")],
             examples=["exp=SRX2716515&feature=Hsa-Mir-486_5p", "exp=SRX2716515,SRX2716516&format=tsv"],
             returns="JSON: experiment -> miRNA -> isomiR class -> read count. TSV/CSV: one row per experiment, miRNA "
                     "and class.", selects="required"),
    Endpoint("trna-fragments", "tRNA fragments", "Read counts per fragment class (5′ halves, tRF-5, i-tRF, pre-tRNA "
                                                 "fragments …) for each tRNA.",
             params=[("reference", "trna-nuclear", "trna-nuclear or trna-mito."),
                     ("feature", "all", "Only these tRNAs (comma-separated).")],
             examples=["exp=SRX2716515&feature=rna-TRE-CTC1-1", "exp=SRX2716515&format=tsv"],
             returns="JSON: experiment -> tRNA -> class -> {read_count, adjusted_read_count, read_count_percent, "
                     "adjusted_read_count_percent}. TSV/CSV: one row per experiment, tRNA and class.",
             selects="required"),
    Endpoint("seed-expression", "Seed expression", "miRNA expression summed per seed (nucleotides 2–8).",
             params=[("reference", "mirgenedb", "mirgenedb, mirbase or pmiren."), VALUE_DOC],
             examples=["exp=SRX2716515,SRX2716516&format=tsv"],
             returns="Like expression-matrix, with seed sequences instead of miRNA names.", selects="required"),
    Endpoint("feature-names", "Feature names", "The miRNA or tRNA names of a reference with the number of "
                                               "experiments that have reads, to find valid names.",
             params=[REFERENCE_DOC, ("q", "", "Only names containing this text (case-insensitive)."),
                     ("limit", "1000", "Number of names, at most 100,000.")],
             examples=["q=Mir-21", "reference=mirbase&q=hsa-mir-21", "reference=trna-mito&limit=10"],
             returns="JSON: list of {name, experiments}. TSV/CSV: one row per name."),
    Endpoint("filters", "Filters", "The filter fields with their values and how many experiments each value "
                                   "selects (counted within the other filters you give).",
             params=[("field", "all", "Only these fields (comma-separated)."),
                     ("limit", "50", "Values per field, at most 10,000; most frequent first.")],
             examples=["field=tissue,sex&organism=Homo sapiens", "field=organism&limit=10"],
             returns="JSON: list of fields with label, type (values or range) and values. TSV/CSV: one row per field "
                     "and value.", selects="optional"),
    Endpoint("taxa", "Taxa", "Taxa at one rank with the number of organisms, studies, experiments and reads.",
             params=[("rank", "kingdom", "kingdom, phylum, class, order, family, genus or organism."),
                     ("term", "", "Only taxa whose name or lineage contains this text.")],
             examples=["rank=class&term=Mammalia", "rank=organism&tissue=blood"],
             returns="JSON: list of taxa. TSV/CSV: one row per taxon.", selects="optional"),
    Endpoint("studies", "Studies", "Search studies with the filters and text; or the details of one study at "
                                   "studies/<accession>.",
             params=[("limit", "100", "Number of studies, at most 10,000."), ("offset", "0", "Paging.")],
             examples=["q=asthma&limit=5", "organism=Mus musculus&format=tsv"],
             returns="JSON: count and studies {study, title, organisms, experiments_matching, experiments_total}. "
                     "studies/SRP103227: title, abstract, PubMed ids, groups and experiments.", selects="optional"),
    Endpoint("selections", "Saved selections", "The experiments of a selection saved on the website: "
                                               "selections/<id>.",
             examples=[], returns="JSON: name, description, saved, experiments (and those no longer in miSRA).",
             formats=False),
]
ENDPOINT_BY_NAME = {e.name: e for e in ENDPOINTS}
