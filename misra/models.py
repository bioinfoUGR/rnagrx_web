import secrets

from django.db import models
from django.urls import reverse


class FacetDefinition(models.Model):
    """Which fields appear in the filter panel, with which label and in which order.

    The available keys are the fields in misra.catalog.FIELDS (categorical or numeric).
    Filters on disabled facets still work through the URL (e.g. the taxonomy ranks).
    """

    WIDGETS = [("checkbox", "list of values"), ("range", "number range")]

    key = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=100)
    group = models.CharField(max_length=60, blank=True, help_text="Heading the facet is shown under")
    widget = models.CharField(max_length=20, choices=WIDGETS, default="checkbox")
    enabled = models.BooleanField(default=True)
    open_by_default = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=100)
    top_n = models.PositiveSmallIntegerField(default=8, help_text="Values listed before 'show more'")
    hidden_values = models.JSONField(default=list, blank=True, help_text="Values not listed (still filterable)")
    help_text = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self):
        return self.label


class IndexBuild(models.Model):
    """One run of `manage.py misra_build_index`. The newest finished build is the one in use."""

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, default="running")  # running | done | failed
    n_experiments = models.PositiveIntegerField(default=0)
    n_studies = models.PositiveIntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"build {self.pk} ({self.status})"


class ExperimentRow(models.Model):
    """Flat, readable copy of one experiment (rebuilt with every index build).

    Used for full-text search (FULLTEXT index on search_text, MariaDB only) and the
    metadata download; filtering and counting use the in-memory arrays (misra.engine).
    """

    exp_id = models.CharField(max_length=20, primary_key=True)
    study = models.CharField(max_length=20, db_index=True)
    taxon_id = models.CharField(max_length=20, null=True)
    organism = models.CharField(max_length=255, null=True)
    tax_kingdom = models.CharField(max_length=100, null=True)
    tax_phylum = models.CharField(max_length=100, null=True)
    tax_class = models.CharField(max_length=100, null=True)
    tax_order = models.CharField(max_length=100, null=True)
    tax_family = models.CharField(max_length=100, null=True)
    tax_genus = models.CharField(max_length=100, null=True)
    tax_species = models.CharField(max_length=100, null=True)
    sample_class = models.CharField(max_length=100, null=True)
    tissue = models.CharField(max_length=255, null=True)
    organism_part = models.CharField(max_length=255, null=True)
    primary_entity = models.CharField(max_length=255, null=True)
    cell_type = models.CharField(max_length=255, null=True)
    cell_line = models.CharField(max_length=255, null=True)
    disease_state = models.CharField(max_length=255, null=True)
    cancer_sample = models.CharField(max_length=3, null=True)
    cancer_study = models.CharField(max_length=3, null=True)
    sex = models.CharField(max_length=100, null=True)
    developmental_stage = models.CharField(max_length=255, null=True)
    protocol = models.CharField(max_length=100, null=True)
    rna_extraction = models.CharField(max_length=255, null=True)
    layout = models.CharField(max_length=20, null=True)
    availability = models.CharField(max_length=60, null=True)
    treatment = models.TextField(null=True)
    age = models.CharField(max_length=255, null=True)
    genotype = models.TextField(null=True)
    read_count = models.FloatField(null=True)
    raw_read_count = models.FloatField(null=True)
    mirna_pct = models.FloatField(null=True)
    trna_pct = models.FloatField(null=True)
    rrna_pct = models.FloatField(null=True)
    genome_pct = models.FloatField(null=True)
    bacteria_pct = models.FloatField(null=True)
    virus_pct = models.FloatField(null=True)
    created_at = models.DateTimeField(null=True)
    search_text = models.TextField(blank=True)

    class Meta:
        db_table = "misra_exp_index"


class StudyRow(models.Model):
    study_id = models.CharField(max_length=20, primary_key=True)
    title = models.TextField(blank=True)
    abstract = models.TextField(blank=True)
    pubmed_ids = models.CharField(max_length=500, blank=True)
    n_experiments = models.PositiveIntegerField(default=0)
    organisms = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "misra_study_index"


class Cart(models.Model):
    """Anonymous experiment cart, linked to the browser session (session stores the cart id)."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    version = models.PositiveIntegerField(default=0)  # bumped on every change (cache key)

    def __str__(self):
        return f"cart {self.pk}"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    exp_id = models.CharField(max_length=20)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cart", "exp_id"], name="uniq_cart_exp")]


def _slug():
    return secrets.token_urlsafe(9)  # 12 characters, not guessable


class Selection(models.Model):
    """A saved, frozen list of experiments with a permanent link."""

    slug = models.CharField(max_length=20, unique=True, default=_slug)
    name = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    exp_ids = models.TextField(help_text="Accessions separated by spaces")
    n_experiments = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name or self.slug

    def id_list(self) -> list[str]:
        return self.exp_ids.split()

    def get_absolute_url(self):
        return reverse("misra:selection", args=[self.slug])
