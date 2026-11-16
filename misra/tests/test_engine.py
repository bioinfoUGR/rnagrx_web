from django.http import QueryDict
from django.test import SimpleTestCase

from misra.engine import Catalog, Query
from misra.query import BrowseQuery

from .factories import sample_records, sample_studies


def catalog():
    return Catalog(Catalog.build(sample_records(), sample_studies()), build_id=1)


class EngineTests(SimpleTestCase):
    def setUp(self):
        self.cat = catalog()

    def ids(self, q):
        return self.cat.exp_ids(self.cat.evaluate(q).mask)

    def test_sorted_by_accession(self):
        self.assertEqual(list(self.cat.exp_id), ["ERX006", "SRX001", "SRX002", "SRX003", "SRX004", "SRX005"])

    def test_or_within_and_across_facets(self):
        q = Query(filters={"tissue": ["blood", "brain"], "organism": ["Homo sapiens"]})
        self.assertEqual(self.ids(q), ["SRX001", "SRX002", "SRX003"])
        q = Query(filters={"tissue": ["blood"], "organism": ["Mus musculus"]})
        self.assertEqual(self.ids(q), ["SRX005"])

    def test_not_annotated_value(self):
        self.assertEqual(self.ids(Query(filters={"tissue": [""]})), ["ERX006"])

    def test_unknown_value_matches_nothing(self):
        self.assertEqual(self.ids(Query(filters={"tissue": ["no such tissue"]})), [])

    def test_ranges_exclude_missing(self):
        self.assertEqual(self.ids(Query(ranges={"read_count": (1e6, None)})), ["ERX006", "SRX001", "SRX003", "SRX004"])
        self.assertEqual(self.ids(Query(ranges={"mirna_pct": (None, 20)})), ["ERX006", "SRX002"])

    def test_disjunctive_counts(self):
        ev = self.cat.evaluate(Query(filters={"tissue": ["blood"], "organism": ["Homo sapiens"]}))
        tissue = {v["value"]: v["count"] for v in self.cat.facet_values(ev, "tissue")[0]}
        # tissue counts ignore the tissue filter itself, but respect the organism filter
        self.assertEqual(tissue, {"blood": 2, "brain": 1})
        organism = {v["value"]: v["count"] for v in self.cat.facet_values(ev, "organism")[0]}
        self.assertEqual(organism, {"Homo sapiens": 2, "Mus musculus": 1})

    def test_selected_value_listed_even_without_matches(self):
        ev = self.cat.evaluate(Query(filters={"sex": ["male"], "organism": ["Zea mays"]}))
        values, _ = self.cat.facet_values(ev, "sex", selected=["male"])
        self.assertIn({"value": "male", "count": 0, "selected": True}, values)

    def test_facet_values_sorted_and_limited(self):
        ev = self.cat.evaluate(Query())
        values, n = self.cat.facet_values(ev, "tissue", limit=2)
        self.assertEqual(n, 4)  # blood, brain, liver, not annotated
        self.assertEqual([v["value"] for v in values], ["blood", ""])

    def test_facet_term_search(self):
        ev = self.cat.evaluate(Query())
        values, _ = self.cat.facet_values(ev, "tissue", term="BR")
        self.assertEqual([v["value"] for v in values], ["brain"])

    def test_paging_and_sorting(self):
        mask = self.cat.evaluate(Query()).mask
        top = self.cat.rows(self.cat.page(mask, "reads", desc=True, limit=3))
        self.assertEqual([r["exp_id"] for r in top], ["SRX003", "SRX001", "ERX006"])
        last = self.cat.rows(self.cat.page(mask, "reads", desc=True, offset=5, limit=3))
        self.assertEqual([r["exp_id"] for r in last], ["SRX005"])  # missing values last
        by_org = self.cat.rows(self.cat.page(mask, "organism", limit=6))
        self.assertEqual(by_org[0]["organism"], "Homo sapiens")
        self.assertEqual(by_org[-1]["organism"], "Zea mays")

    def test_study_page(self):
        mask = self.cat.evaluate(Query(filters={"tissue": ["blood"]})).mask
        rows, total = self.cat.study_page(mask)
        self.assertEqual(total, 2)
        self.assertEqual(rows[0], {"study_id": "SRP1", "title": "Human blood and brain", "organisms": "Homo sapiens",
                                   "matching": 2, "total": 3})

    def test_positions_and_scope(self):
        pos = self.cat.positions_of(["SRX004", "nope", "ERX006"])
        self.assertEqual(sorted(self.cat.exp_id[pos]), ["ERX006", "SRX004"])
        q = Query(filters={"sample_class": ["tissue_or_organ"]}, scope_mask=self.cat.mask_of_positions(pos))
        self.assertEqual(self.ids(q), ["ERX006", "SRX004"])

    def test_taxon_table(self):
        mask = self.cat.evaluate(Query()).mask
        rows = {r["value"]: r for r in self.cat.taxon_table(mask, "tax_kingdom", [])}
        self.assertEqual(rows["Metazoa"]["organisms"], 2)
        self.assertEqual(rows["Metazoa"]["studies"], 2)
        self.assertEqual(rows["Metazoa"]["experiments"], 5)
        self.assertEqual(rows["Metazoa"]["reads"], 5e6 + 2e5 + 8e6 + 1e6)  # missing reads count as 0
        rows = {r["value"]: r for r in self.cat.taxon_table(mask, "organism", ["tax_kingdom"])}
        self.assertEqual(rows["Zea mays"]["lineage"], ["Viridiplantae"])
        self.assertEqual(rows["Mus musculus"]["experiments"], 2)

    def test_save_and_load(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.npz"
            Catalog.save(Catalog.build(sample_records(), sample_studies()), path)
            loaded = Catalog.load(path, build_id=7)
        self.assertEqual(loaded.n, 6)
        self.assertEqual(loaded.build_id, 7)


class QueryTests(SimpleTestCase):
    def test_parse_and_roundtrip(self):
        params = QueryDict("tab=studies&q=blood&f_tissue=blood&f_tissue=brain&f_tissue=blood&min_read_count=1e6"
                           "&f_bogus=x&sort=nope&page=3")
        bq = BrowseQuery.from_params(params)
        self.assertEqual(bq.tab, "studies")
        self.assertEqual(bq.filters, {"tissue": ["blood", "brain"]})
        self.assertEqual(bq.ranges, {"read_count": (1e6, None)})
        self.assertEqual(bq.sort, "matching")
        self.assertEqual(bq.page, 3)
        again = BrowseQuery.from_params(QueryDict(bq.urlencode(page=3)))
        self.assertEqual((again.tab, again.text, again.filters, again.ranges, again.page),
                         (bq.tab, bq.text, bq.filters, bq.ranges, 3))

    def test_without(self):
        bq = BrowseQuery.from_params(QueryDict("f_tissue=blood&f_tissue=brain&q=x&max_mirna_pct=5"))
        self.assertEqual(BrowseQuery.from_params(QueryDict(bq.without("tissue", "blood"))).filters,
                         {"tissue": ["brain"]})
        self.assertEqual(BrowseQuery.from_params(QueryDict(bq.without("q"))).text, "")
        self.assertEqual(BrowseQuery.from_params(QueryDict(bq.without("mirna_pct"))).ranges, {})

    def test_tab_switch_resets_sort(self):
        bq = BrowseQuery.from_params(QueryDict("sort=reads&desc=1&f_sex=male"))
        self.assertEqual(bq.urlencode(tab="studies"), "tab=studies&f_sex=male")
