from django.test import TestCase, override_settings
from django.urls import reverse

from misra import index_store
from misra.indexer import build_index
from misra.models import CartItem, ExperimentRow, Selection

from .factories import sample_records, sample_studies


class BrowseViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        build_index(sample_records(), sample_studies())

    def setUp(self):
        index_store.reset_for_tests()

    def test_build_wrote_tables_and_stats(self):
        self.assertEqual(ExperimentRow.objects.count(), 6)
        build = index_store.current_build()
        self.assertEqual(build.n_experiments, 6)
        self.assertEqual(build.n_studies, 3)
        self.assertEqual(build.stats["top"]["organism"][0], ["Homo sapiens", 3])

    def test_landing_and_home(self):
        self.assertContains(self.client.get(reverse("core:landing")), "miSRA")
        self.assertContains(self.client.get(reverse("misra:home")), "experiments")

    def test_browse_full_page_and_partial(self):
        url = reverse("misra:browse") + "?f_tissue=blood"
        r = self.client.get(url)
        self.assertContains(r, "SRX005")
        self.assertNotContains(r, "SRX003")
        self.assertContains(r, "<html", html=False)
        r = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertContains(r, "SRX005")
        self.assertNotContains(r, "<html", html=False)
        self.assertIn("HX-Request", r["Vary"])

    def test_text_search_uses_study_abstract(self):
        r = self.client.get(reverse("misra:browse") + "?q=exosome")
        self.assertContains(r, "SRX001")
        self.assertNotContains(r, "SRX004")

    def test_studies_tab(self):
        r = self.client.get(reverse("misra:browse") + "?tab=studies&f_tissue=blood")
        self.assertContains(r, "Human blood and brain")
        self.assertNotContains(r, "Maize drought")

    def test_facet_values_endpoint(self):
        r = self.client.get(reverse("misra:facet_values", args=["tissue"]) + "?term=bl")
        self.assertContains(r, 'value="blood"')
        self.assertNotContains(r, 'value="liver"')

    def test_taxonomy(self):
        r = self.client.get(reverse("misra:taxonomy"))
        self.assertContains(r, "Viridiplantae")
        r = self.client.get(reverse("misra:taxonomy") + "?f_tax_kingdom=Metazoa")
        self.assertContains(r, "5 experiments")

    def test_explore(self):
        self.assertContains(self.client.get(reverse("misra:explore")), "Homo sapiens")

    def test_cart_add_query_ids_remove_keep_save(self):
        r = self.client.post(reverse("misra:cart_add"), {"mode": "query", "f_organism": "Homo sapiens", "scope": "all"})
        self.assertContains(r, "3 experiments added")
        r = self.client.post(reverse("misra:cart_add"), {"mode": "ids", "exp": ["SRX001", "SRX004"]})
        self.assertContains(r, "1 experiment added")
        self.assertEqual(CartItem.objects.count(), 4)

        r = self.client.get(reverse("misra:cart"))
        self.assertContains(r, "4 experiments")
        r = self.client.get(reverse("misra:cart") + "?f_tissue=brain", HTTP_HX_REQUEST="true")
        self.assertContains(r, "SRX003")
        self.assertNotContains(r, "SRX004")

        r = self.client.post(reverse("misra:cart_remove"), {"mode": "query", "scope": "cart", "f_tissue": "brain"})
        self.assertEqual(r["HX-Refresh"], "true")
        self.assertEqual(sorted(CartItem.objects.values_list("exp_id", flat=True)), ["SRX001", "SRX002", "SRX004"])

        r = self.client.post(reverse("misra:cart_keep"), {"mode": "query", "scope": "cart", "f_sex": "male"})
        self.assertEqual(list(CartItem.objects.values_list("exp_id", flat=True)), ["SRX001"])

        r = self.client.post(reverse("misra:cart_save"), {"name": "my set"})
        sel = Selection.objects.get()
        self.assertEqual(r["HX-Redirect"], sel.get_absolute_url())
        self.assertEqual(sel.id_list(), ["SRX001"])
        self.assertContains(self.client.get(sel.get_absolute_url()), "my set")

        tsv = b"".join(self.client.get(reverse("misra:selection_tsv", args=[sel.slug])).streaming_content).decode()
        lines = tsv.strip().split("\n")
        self.assertTrue(lines[0].startswith("exp_id\tstudy"))
        self.assertEqual(len(lines), 2)
        self.assertIn("5000000", lines[1])

    @override_settings(MISRA_CART_LIMIT=2)
    def test_cart_limit(self):
        r = self.client.post(reverse("misra:cart_add"), {"mode": "query", "scope": "all"})
        self.assertContains(r, "limited to 2")
        self.assertEqual(CartItem.objects.count(), 2)

    def test_studies_ticked_add(self):
        r = self.client.post(reverse("misra:cart_add"), {"mode": "query", "scope": "all", "f_study": ["SRP2"],
                                                         "f_tissue": "blood"})
        self.assertContains(r, "1 experiment added")
        self.assertEqual(list(CartItem.objects.values_list("exp_id", flat=True)), ["SRX005"])


class NotReadyTests(TestCase):
    def test_browse_without_index(self):
        index_store.reset_for_tests()
        self.assertEqual(self.client.get(reverse("misra:browse")).status_code, 503)
