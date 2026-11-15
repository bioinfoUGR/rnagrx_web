"""Initial filter-panel configuration. Edit it later in the Django admin (miSRA › Facet definitions)."""
from django.db import migrations

# key, label, group, widget, enabled, open_by_default, help_text
FACETS = [
    ("organism", "Organism", "Organism", "checkbox", True, True, ""),
    ("tax_kingdom", "Kingdom", "Organism", "checkbox", True, False, ""),
    ("sample_class", "Sample class", "Sample", "checkbox", True, True, ""),
    ("tissue", "Tissue", "Sample", "checkbox", True, True, ""),
    ("organism_part", "Organism part", "Sample", "checkbox", True, False, ""),
    ("cell_type", "Cell type", "Sample", "checkbox", True, False, ""),
    ("cell_line", "Cell line", "Sample", "checkbox", True, False, ""),
    ("primary_entity", "Primary entity", "Sample", "checkbox", True, False,
     "The most specific sample description given by the pipeline."),
    ("disease_state", "Disease state", "Sample", "checkbox", True, False, ""),
    ("cancer_sample", "Cancer sample", "Sample", "checkbox", True, False, ""),
    ("cancer_study", "From a cancer study", "Sample", "checkbox", True, False, ""),
    ("sex", "Sex", "Sample", "checkbox", True, False, ""),
    ("developmental_stage", "Developmental stage", "Sample", "checkbox", True, False, ""),
    ("protocol", "Library protocol (inferred)", "Library", "checkbox", True, False,
     "Guessed by sRNAbench from the adapter sequences."),
    ("rna_extraction", "RNA extraction", "Library", "checkbox", True, False, ""),
    ("layout", "Read layout", "Library", "checkbox", True, False, ""),
    ("availability", "Processing results", "Quality", "checkbox", True, False, ""),
    ("read_count", "Reads after preprocessing", "Quality", "range", True, False, ""),
    ("mirna_pct", "miRNA reads (%)", "Quality", "range", True, False, ""),
    ("genome_pct", "Genome-mapped reads (%)", "Quality", "range", True, False, ""),
    ("trna_pct", "tRNA reads (%)", "Quality", "range", False, False, ""),
    ("rrna_pct", "rRNA reads (%)", "Quality", "range", False, False, ""),
    ("bacteria_pct", "Bacteria-mapped reads (%)", "Quality", "range", False, False, ""),
    ("virus_pct", "Virus-mapped reads (%)", "Quality", "range", False, False, ""),
    ("raw_read_count", "Raw reads", "Quality", "range", False, False, ""),
]


def seed(apps, schema_editor):
    FacetDefinition = apps.get_model("misra", "FacetDefinition")
    for order, (key, label, group, widget, enabled, open_, help_text) in enumerate(FACETS, start=1):
        FacetDefinition.objects.update_or_create(key=key, defaults={
            "label": label, "group": group, "widget": widget, "enabled": enabled,
            "open_by_default": open_, "order": order * 10, "help_text": help_text,
        })


def unseed(apps, schema_editor):
    apps.get_model("misra", "FacetDefinition").objects.filter(key__in=[f[0] for f in FACETS]).delete()


class Migration(migrations.Migration):
    dependencies = [("misra", "0002_fulltext")]
    operations = [migrations.RunPython(seed, unseed)]
