from django.test import TestCase
from django.urls import reverse

from jobs.models import Job


class PageTests(TestCase):
    def test_home_lists_tools(self):
        r = self.client.get(reverse("srnatoolbox:home"))
        self.assertContains(r, "sRNAbench")
        self.assertContains(r, "sRNAde")
        self.assertContains(r, "coming soon")
        self.assertContains(r, "sRNAblast")  # planned tools
        self.assertContains(r, "https://arn.ugr.es/srnatoolbox/")

    def test_about(self):
        r = self.client.get(reverse("srnatoolbox:about"))
        self.assertContains(r, "https://doi.org/10.1093/nar/gkac363")

    def test_tool_pages(self):
        self.assertContains(self.client.get(reverse("srnatoolbox:tool", args=["srnabench"])), "starts from a dataset")
        self.assertContains(self.client.get(reverse("srnatoolbox:tool", args=["srnade"])), "coming soon")
        self.assertEqual(self.client.get(reverse("srnatoolbox:tool", args=["nope"])).status_code, 404)

    def test_landing_links_both_services(self):
        r = self.client.get(reverse("core:landing"))
        self.assertContains(r, reverse("srnatoolbox:home"))
        self.assertContains(r, reverse("misra:home"))


class JobPageTests(TestCase):
    def test_job_page_in_toolbox_chrome(self):
        parent = Job.objects.create(app="srnatoolbox", tool="srnabench", n_items=4, status=Job.DONE)
        job = Job.objects.create(app="srnatoolbox", tool="srnade", n_items=4, parent=parent, status=Job.FAILED,
                                 message="something went wrong")
        self.assertEqual(job.get_absolute_url(), f"/srnatoolbox/job/{job.slug}/")
        r = self.client.get(job.get_absolute_url())
        self.assertContains(r, "sRNAde: differential expression")
        self.assertContains(r, "something went wrong")
        self.assertContains(r, parent.get_absolute_url())
        self.assertContains(r, "brand-srna")
        # remembered for the home page; a miSRA job is not served under /srnatoolbox/
        self.assertContains(self.client.get(reverse("srnatoolbox:home")), job.slug)
        misra_job = Job.objects.create(app="misra", tool="matrix")
        self.assertEqual(self.client.get(reverse("srnatoolbox:job", args=[misra_job.slug])).status_code, 404)
