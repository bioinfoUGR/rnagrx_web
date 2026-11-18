import io
from datetime import timedelta
from pathlib import Path

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from jobs.models import Job
from jobs.registry import Tool, get_tool, register, tools_of
from jobs.tasks import run_job


def ok_runner(job, directory: Path, log):
    log("working")
    (directory / "out.txt").write_text("result\n")
    return [{"name": "out.txt", "label": "The result"}]


def failing_runner(job, directory: Path, log):
    raise ValueError("bad input")


register(
    Tool(app="testapp", key="ok", label="OK", title="OK tool", description="", runner="jobs.tests.test_jobs.ok_runner"),
    Tool(app="testapp", key="fail", label="Fail", title="Failing tool", description="",
         runner="jobs.tests.test_jobs.failing_runner"),
)


class RegistryTests(TestCase):
    def test_lookup(self):
        self.assertEqual(get_tool("testapp", "ok").title, "OK tool")
        self.assertIsNone(get_tool("testapp", "nope"))
        self.assertEqual(list(tools_of("testapp")), ["ok", "fail"])
        self.assertIn("matrix", tools_of("misra"))  # registered by MisraConfig.ready()
        self.assertEqual(get_tool("misra", "matrix").queue, "rnagrx_quick")


class RunJobTests(TestCase):
    def test_done(self):
        job = Job.objects.create(app="testapp", tool="ok")
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.DONE)
        self.assertEqual(job.results, [{"name": "out.txt", "label": "The result", "size": 7}])
        log = (job.directory() / "job.log").read_text()
        self.assertIn("working", log)
        self.assertIn("finished", log)

    def test_failed(self):
        job = Job.objects.create(app="testapp", tool="fail")
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.FAILED)
        self.assertEqual(job.message, "bad input")

    def test_unknown_tool(self):
        job = Job.objects.create(app="testapp", tool="gone")
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.FAILED)
        self.assertIn("unknown tool", job.message)

    def test_runs_only_queued_jobs(self):
        job = Job.objects.create(app="testapp", tool="ok", status=Job.RUNNING)
        run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.RUNNING)


class JobPageTests(TestCase):
    def test_page_and_files_stay_in_their_service(self):
        job = Job.objects.create(app="misra", tool="matrix", n_items=3, inputs={"exp_ids": [], "source": "cart"},
                                 status=Job.DONE, results=[{"name": "x.tsv", "label": "X", "size": 2}])
        job.directory().mkdir(parents=True)
        (job.directory() / "x.tsv").write_text("x\n")
        self.assertEqual(job.get_absolute_url(), f"/misra/job/{job.slug}/")
        page = self.client.get(job.get_absolute_url())
        self.assertContains(page, "miRNA expression matrix")
        self.assertContains(page, "(from a cart)")
        self.assertContains(page, reverse("misra:job_file", args=[job.slug, "x.tsv"]))
        r = self.client.get(reverse("misra:job_file", args=[job.slug, "x.tsv"]))
        self.assertEqual(b"".join(r.streaming_content), b"x\n")
        # a job of another service is not found under /misra/
        other = Job.objects.create(app="testapp", tool="ok")
        self.assertEqual(self.client.get(reverse("misra:job", args=[other.slug])).status_code, 404)

    def test_recent_jobs_in_session(self):
        job = Job.objects.create(app="misra", tool="matrix", n_items=1)
        self.client.get(job.get_absolute_url())
        self.assertContains(self.client.get(reverse("misra:tools")), job.slug)


class CleanupTests(TestCase):
    def test_expiry_and_stuck_jobs(self):
        old = Job.objects.create(app="misra", tool="matrix", status=Job.DONE,
                                 results=[{"name": "x", "label": "x", "size": 1}])
        old.directory().mkdir(parents=True)
        (old.directory() / "x").write_text("x")
        stuck = Job.objects.create(app="misra", tool="matrix", status=Job.RUNNING)
        fresh = Job.objects.create(app="misra", tool="matrix", status=Job.DONE)
        Job.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=15))
        Job.objects.filter(pk=stuck.pk).update(created_at=timezone.now() - timedelta(days=3))
        call_command("jobs_cleanup", stdout=io.StringIO())
        for j in (old, stuck, fresh):
            j.refresh_from_db()
        self.assertEqual(old.status, Job.EXPIRED)
        self.assertEqual(old.results, [])
        self.assertFalse(old.directory().exists())
        self.assertEqual(stuck.status, Job.FAILED)
        self.assertEqual(fresh.status, Job.DONE)


class CopyMisraJobsMigrationTests(TransactionTestCase):
    """jobs 0002 copies misra_job rows with id, slug and creation time."""

    before = [("misra", "0004_job"), ("jobs", "0001_initial")]
    after = [("misra", "0004_job"), ("jobs", "0002_copy_misra_jobs")]

    def test_copy(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old_apps = executor.loader.project_state(self.before).apps
        OldJob = old_apps.get_model("misra", "Job")
        created = timezone.now() - timedelta(days=5)
        o = OldJob.objects.create(slug="abcdefghijkl", tool="matrix", status="done", params={"db": "mgdb"},
                                  exp_ids="SRX1 SRX2", n_experiments=2, source="cart",
                                  results=[{"name": "m.tsv", "label": "M", "size": 3}])
        OldJob.objects.filter(pk=o.pk).update(created_at=created)

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        NewJob = executor.loader.project_state(self.after).apps.get_model("jobs", "Job")
        n = NewJob.objects.get(slug="abcdefghijkl")
        self.assertEqual(n.pk, o.pk)
        self.assertEqual((n.app, n.tool, n.status, n.n_items), ("misra", "matrix", "done", 2))
        self.assertEqual(n.inputs, {"exp_ids": ["SRX1", "SRX2"], "source": "cart"})
        self.assertEqual(n.params, {"db": "mgdb"})
        self.assertEqual(n.created_at, created)

        # back to the latest state for the other tests
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
