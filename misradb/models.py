"""Read-only models for the miSRA database (misradb2), which the processing pipeline writes.

managed = False: Django never creates or changes these tables. The router in
config/routers.py sends all queries to the "misra" database alias.
Numeric columns use -1 as "not available"; see misra.catalog for the conversion.
"""
import json

from django.db import models


class JSONTextField(models.TextField):
    """longtext column holding JSON (MariaDB's JSON type is longtext + CHECK)."""

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return None
        try:
            return json.loads(value)
        except ValueError:
            return None


class Study(models.Model):
    id = models.CharField(max_length=20, primary_key=True)
    title = models.TextField(null=True)
    abstract = models.TextField(null=True)
    pubmed_ids = JSONTextField(null=True)
    taxon_ids = JSONTextField(null=True)
    grouping_vars = JSONTextField(null=True)
    has_small_rna_seq = models.BooleanField(null=True)
    has_transcriptomic_rna_seq = models.BooleanField(null=True)
    full_ids = JSONTextField(null=True)
    no_annot_ids = JSONTextField(null=True)
    fail_ids = JSONTextField(null=True)
    created_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "studies"

    def __str__(self):
        return self.id


class Taxonomy(models.Model):
    taxon_id = models.CharField(max_length=20, primary_key=True)
    scientific_name = models.CharField(max_length=255)
    taxon_rank = models.CharField(max_length=50)
    kingdom_name = models.CharField(max_length=100, null=True)
    phylum_name = models.CharField(max_length=100, null=True)
    class_name = models.CharField(max_length=100, null=True)
    order_name = models.CharField(max_length=100, null=True)
    family_name = models.CharField(max_length=100, null=True)
    genus_name = models.CharField(max_length=100, null=True)
    species_name = models.CharField(max_length=100, null=True)
    lineage_search = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "taxonomy"

    def __str__(self):
        return self.scientific_name


class Experiment(models.Model):
    id = models.CharField(max_length=20, primary_key=True)
    study = models.ForeignKey(Study, on_delete=models.DO_NOTHING, db_column="study_id", related_name="experiments")
    taxon_id = models.CharField(max_length=20, null=True)
    primary_entity = models.CharField(max_length=100, null=True)
    sample_class = models.CharField(max_length=100, null=True)
    tissue = models.CharField(max_length=100, null=True)
    read_count = models.BigIntegerField(null=True)
    adapter_dimer_count = models.BigIntegerField(null=True)
    short_read_count = models.BigIntegerField(null=True)
    raw_read_count = models.BigIntegerField(null=True)
    mirna_percent = models.FloatField(null=True)
    trna_percent = models.FloatField(null=True)
    rrna_percent = models.FloatField(null=True)
    perc_genome_mapped = models.FloatField(null=True)
    perc_bacteria_mapped = models.FloatField(null=True)
    perc_virus_mapped = models.FloatField(null=True)
    rna_extraction_method = models.CharField(max_length=100, null=True)
    protocol_guess = models.CharField(max_length=50, null=True)
    is_paired_end = models.BooleanField(null=True)
    from_cancer_study = models.BooleanField(null=True)
    annotations = JSONTextField(null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "experiments"

    def __str__(self):
        return self.id


class ExperimentAvailable(models.Model):
    exp_id = models.CharField(max_length=20, primary_key=True)
    state = models.CharField(max_length=20, null=True)

    class Meta:
        managed = False
        db_table = "experiments_available"


class ExperimentAnalysis(models.Model):
    experiment_id = models.CharField(max_length=20, primary_key=True)
    preproc_results = JSONTextField(null=True)
    profiling_results = JSONTextField(null=True)
    read_lengths = JSONTextField(null=True)
    inferred_metadata = JSONTextField(null=True)
    grouping_annotations = JSONTextField(null=True)

    class Meta:
        managed = False
        db_table = "experiment_analysis"


class ReadFile(models.Model):
    id = models.AutoField(primary_key=True)
    exp_id = models.CharField(max_length=20)
    relative_path = models.CharField(max_length=512)
    file_type = models.CharField(max_length=50)
    db_name = models.CharField(max_length=50, null=True)
    qualifier = models.CharField(max_length=50, null=True)
    layout = models.CharField(max_length=2, null=True)
    preprocessed_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "read_files"
        ordering = ["file_type", "db_name", "qualifier"]
