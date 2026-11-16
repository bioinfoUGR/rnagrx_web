import json

from django.db import connections
from django.test import TestCase, override_settings
from django.urls import reverse

from misra import index_store
from misra.indexer import build_index
from misra.models import Selection

from .factories import sample_records, sample_studies

API = "/misra/api/v1/"

ISO = json.dumps({"canonical": 60.0, "nta#A": 40.0})
FRAG = json.dumps({"5p_half": {"rc": 9.0, "rcadj": 4.5, "rc_perc": 90.0, "rcadj_perc": 90.0}})
PREPROC = json.dumps({"misra_preproc": {"exp": "SRX001", "protocol_name": "NEB", "raw_reads": 6000000.0,
                                        "adapter_dimer_reads": 10}})
PROFILING = json.dumps([{"rna_type": "miRNA_+", "ur": "10.0", "ur_perc": "5.5", "rc": "900.0", "rc_perc": "60.0"}])
LENGTHS = json.dumps({"read_length": [{"Read length": "22", "ur": "5", "ur_perc": "50", "rc": "800", "rc_perc": "80",
                                       "rpm": "800000"}]})


class ApiTests(TestCase):
    databases = {"default", "misra"}

    @classmethod
    def setUpTestData(cls):
        build_index(sample_records(), sample_studies())
        with connections["misra"].cursor() as c:
            for table, name_col, extra in (("mgdb", "mirna_name", "seed TEXT, iso_mirs TEXT"),
                                           ("nuc_trna", "trna_name", "fragment_class TEXT")):
                c.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, exp_id TEXT, {name_col} TEXT, rc REAL, "
                          f"rc_adj REAL, rpm_lib REAL, rpm_total REAL, {extra})")
            mg = [("SRX001", "Hsa-Mir-21_5p", 100, 100, 1000, 50, "AGCTTAT", ISO),
                  ("SRX001", "Hsa-Let-7-P1_5p", 10, 10, 100, 5, "GAGGTAG", ISO),
                  ("SRX002", "Hsa-Mir-21_5p", 300, 300, 3000, 150, "AGCTTAT", ISO),
                  ("SRX004", "Mmu-Mir-21_5p", 20, 20, 200, 10, "AGCTTAT", None)]
            c.executemany("INSERT INTO mgdb (exp_id, mirna_name, rc, rc_adj, rpm_lib, rpm_total, seed, iso_mirs) "
                          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", mg)
            c.execute("INSERT INTO nuc_trna (exp_id, trna_name, rc, rc_adj, rpm_lib, rpm_total, fragment_class) "
                      "VALUES ('SRX001', 'tRNA-Glu-CTC', 10, 5, 100, 4, %s)", [FRAG])
            c.execute("CREATE TABLE experiments (id TEXT PRIMARY KEY, raw_read_count INTEGER, adapter_dimer_count "
                      "INTEGER, short_read_count INTEGER, read_count INTEGER, mirna_percent REAL, trna_percent REAL, "
                      "rrna_percent REAL, perc_genome_mapped REAL, perc_bacteria_mapped REAL, perc_virus_mapped REAL)")
            c.execute("INSERT INTO experiments VALUES ('SRX001', 6000000, 10, 5, 5000000, 40, 2, 7, 80, 0.2, -1)")
            c.execute("CREATE TABLE experiment_analysis (experiment_id TEXT PRIMARY KEY, preproc_results TEXT, "
                      "profiling_results TEXT, read_lengths TEXT)")
            c.execute("INSERT INTO experiment_analysis VALUES ('SRX001', %s, %s, %s)", [PREPROC, PROFILING, LENGTHS])
            c.execute("CREATE TABLE studies (id TEXT PRIMARY KEY, title TEXT, abstract TEXT, pubmed_ids TEXT, "
                      "taxon_ids TEXT, grouping_vars TEXT, has_small_rna_seq INTEGER, has_transcriptomic_rna_seq INTEGER, "
                      "full_ids TEXT, no_annot_ids TEXT, fail_ids TEXT, created_at TEXT, completed_at TEXT)")
            c.execute("INSERT INTO studies (id, title, abstract, pubmed_ids, grouping_vars) VALUES "
                      "('SRP1', 'Human blood and brain', 'Plasma exosome profiling.', '[\"123\"]', '{\"sex\": [\"male\"]}')")

    def setUp(self):
        index_store.reset_for_tests()

    def get(self, path, status=200, **params):
        r = self.client.get(API + path, params)
        body = b"" if r.streaming else r.content[:500]
        self.assertEqual(r.status_code, status, body)
        return r

    def tsv(self, path, **params):
        r = self.get(path, format="tsv", **params)
        return [line.split("\t") for line in b"".join(r.streaming_content).decode().strip().split("\n")]

    def test_help_page_and_database(self):
        r = self.client.get(reverse("misra:api_help"))
        self.assertContains(r, "expression-matrix")
        self.assertContains(r, "top-experiments?feature=Hsa-Mir-122_5p")
        d = self.get("database").json()
        self.assertEqual(d["experiments"], 6)
        self.assertIn("trna-nuclear", d["references"])
        self.assertEqual(len(d["endpoints"]), 16)

    def test_errors_are_json_with_status(self):
        r = self.get("expression-matrix", 400)
        self.assertIn("exp=", r.json()["error"])
        self.assertTrue(r.json()["help"].endswith("#expression-matrix"))
        self.assertEqual(r["Access-Control-Allow-Origin"], "*")
        self.assertIn("Unknown parameter", self.get("experiments", 400, tisue="blood").json()["error"])
        self.assertIn("Unknown reference", self.get("expression-matrix", 400, exp="SRX001", reference="x").json()["error"])
        self.assertIn("whole number", self.get("experiments", 400, limit="abc").json()["error"])
        self.get("nope", 404)
        self.assertEqual(self.client.post(API + "database").status_code, 405)

    def test_experiments_search(self):
        d = self.get("experiments", organism="Homo sapiens", tissue="blood").json()
        self.assertEqual(d["count"], 2)
        self.assertEqual([e["exp_id"] for e in d["experiments"]], ["SRX001", "SRX002"])
        d = self.get("experiments", f_tissue="blood", f_organism="Mus musculus", fields="exp_id,read_count").json()
        self.assertEqual(d["experiments"], [{"exp_id": "SRX005", "read_count": None}])
        rows = self.tsv("experiments", study="SRP1", fields="exp_id,sex")
        self.assertEqual(rows, [["exp_id", "sex"], ["SRX001", "male"], ["SRX002", "female"], ["SRX003", "male"]])
        d = self.get("experiments", exp="SRX001,SRX999", min_read_count=1000000).json()
        self.assertEqual((d["count"], d["not_found"]), (1, ["SRX999"]))
        self.assertEqual(self.get("experiments", taxon="Viridiplantae").json()["count"], 1)
        self.get("experiments", 404, taxon="Nowhere")
        self.assertEqual(self.get("experiments", limit=2, offset=1).json()["experiments"][0]["exp_id"], "SRX001")

    def test_experiment_summary(self):
        d = self.get("experiment-summary", exp="SRX001").json()["experiments"]["SRX001"]
        self.assertEqual(d["reads"]["raw_reads"], 6000000)
        self.assertEqual(d["percent"]["virus"], None)  # -1 in misradb2
        self.assertEqual(d["preprocessing"]["protocol_name"], "NEB")
        self.assertEqual(d["metadata"]["tissue"], "blood")
        rows = self.tsv("experiment-summary", exp="SRX001")
        self.assertIn("preprocessing_protocol_name", rows[0])

    def test_rna_composition_and_read_lengths(self):
        d = self.get("rna-composition", exp="SRX001").json()["experiments"]["SRX001"]
        self.assertEqual(d, [{"rna_type": "miRNA_+", "unique_reads": 10, "unique_reads_percent": 5.5,
                              "read_count": 900, "read_count_percent": 60}])
        rows = self.tsv("read-lengths", exp="SRX001")
        self.assertEqual(rows[1], ["SRX001", "22", "5", "50", "800", "80", "800000"])

    def test_expression_matrix(self):
        d = self.get("expression-matrix", exp="SRX001,SRX002,SRX003", value="read_count").json()
        self.assertEqual(d["features"], ["Hsa-Let-7-P1_5p", "Hsa-Mir-21_5p"])
        self.assertEqual(d["experiments"], ["SRX001", "SRX002"])
        self.assertEqual(d["data"], [[10.0, 0.0], [100.0, 300.0]])
        self.assertEqual(d["experiments_without_values"], ["SRX003"])
        rows = self.tsv("expression-matrix", study="SRP1", feature="Hsa-Mir-21_5p")
        self.assertEqual(rows, [["miRNA", "SRX001", "SRX002"], ["Hsa-Mir-21_5p", "50", "150"]])
        rows = self.tsv("expression-matrix", exp="SRX001", reference="trna-nuclear")
        self.assertEqual(rows, [["tRNA", "SRX001"], ["tRNA-Glu-CTC", "4"]])
        with override_settings(MISRA_API_MATRIX_LIMIT=2):
            self.get("expression-matrix", 413, study="SRP1")

    def test_seed_expression(self):
        d = self.get("seed-expression", exp="SRX001", value="read_count").json()
        self.assertEqual(dict(zip(d["features"], d["data"])), {"AGCTTAT": [100.0], "GAGGTAG": [10.0]})
        self.get("seed-expression", 400, exp="SRX001", reference="trna-nuclear")

    def test_isomirs_and_trna_fragments(self):
        d = self.get("isomirs", exp="SRX001", feature="Hsa-Mir-21_5p").json()
        self.assertEqual(d["experiments"], {"SRX001": {"Hsa-Mir-21_5p": {"canonical": 60, "nta#A": 40}}})
        rows = self.tsv("isomirs", exp="SRX001")
        self.assertEqual(rows[0], ["experiment", "miRNA", "class", "read_count"])
        self.assertEqual(len(rows), 5)
        d = self.get("trna-fragments", exp="SRX001").json()
        self.assertEqual(d["experiments"]["SRX001"]["tRNA-Glu-CTC"]["5p_half"]["adjusted_read_count"], 4.5)

    def test_top_experiments(self):
        d = self.get("top-experiments", feature="Hsa-Mir-21_5p").json()
        self.assertEqual([e["experiment"] for e in d["experiments"]], ["SRX002", "SRX001"])
        self.assertEqual(d["experiments"][0]["tissue"], "blood")
        self.assertEqual(d["experiments"][0]["rpm_total"], 150)
        d = self.get("top-experiments", feature="Hsa-Mir-21_5p", sex="male").json()
        self.assertEqual([e["experiment"] for e in d["experiments"]], ["SRX001"])
        self.assertIn("feature-names", self.get("top-experiments", 404, feature="hsa-mir-21").json()["error"])
        self.get("top-experiments", 400, feature="Hsa-Mir-21_5p", limit=5000)

    def test_expression_summary(self):
        d = self.get("expression-summary", feature="Hsa-Mir-21_5p,Nope", organism="Homo sapiens",
                     value="read_count").json()
        s = d["features"]["Hsa-Mir-21_5p"]
        self.assertEqual((s["experiments_selected"], s["experiments_with_reads"], s["sum"]), (3, 2, 400.0))
        self.assertAlmostEqual(s["mean_all"], 400 / 3, places=4)
        self.assertEqual((s["median_with_reads"], s["max"], s["max_experiment"]), (200.0, 300.0, "SRX002"))
        self.assertEqual(d["unknown_features"], ["Nope"])

    def test_feature_names_filters_taxa(self):
        d = self.get("feature-names", q="mir-21").json()
        self.assertEqual(d["features"], [{"name": "Hsa-Mir-21_5p", "experiments": 2},
                                         {"name": "Mmu-Mir-21_5p", "experiments": 1}])
        d = self.get("filters", field="tissue,read_count", organism="Homo sapiens", tissue="blood").json()
        tissue = d["fields"][0]
        self.assertEqual({v["value"]: v["experiments"] for v in tissue["values"]}, {"blood": 2, "brain": 1})
        self.assertEqual(d["fields"][1]["min"], 200000.0)
        self.get("filters", 400, field="nope")
        d = self.get("taxa", rank="organism", term="metazoa").json()
        self.assertEqual([t["name"] for t in d["taxa"]], ["Homo sapiens", "Mus musculus"])

    def test_studies_and_selections(self):
        d = self.get("studies", tissue="blood").json()
        self.assertEqual([s["study"] for s in d["studies"]], ["SRP1", "SRP2"])
        d = self.get("studies/SRP1").json()
        self.assertEqual((d["pubmed_ids"], d["experiments"]), (["123"], ["SRX001", "SRX002", "SRX003"]))
        self.get("studies/SRP999", 404)
        sel = Selection.objects.create(name="s", exp_ids="SRX001 SRX777", n_experiments=2)
        d = self.get(f"selections/{sel.slug}").json()
        self.assertEqual((d["experiments"], d["not_in_current_data"]), (["SRX001"], ["SRX777"]))
        self.assertEqual(self.get("experiments", selection=sel.slug).json()["count"], 1)

    @override_settings(MISRA_API_RATE_PER_MIN=2)
    def test_rate_limit(self):
        self.get("database")
        self.get("database")
        self.assertEqual(self.get("database", 429)["Retry-After"], "60")
