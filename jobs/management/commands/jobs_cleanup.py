import shutil
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.models import Job

STUCK_AFTER_DAYS = 2


class Command(BaseCommand):
    help = (f"Mark jobs that stayed waiting/running for {STUCK_AFTER_DAYS} days as failed, and delete the result "
            "files of jobs older than JOB_MAX_AGE_DAYS days (the job rows stay, marked 'expired').")

    def handle(self, *args, **opts):
        now = timezone.now()
        # jobs whose worker died
        n_stuck = Job.objects.filter(status__in=[Job.QUEUED, Job.RUNNING],
                                     created_at__lt=now - timedelta(days=STUCK_AFTER_DAYS)) \
            .update(status=Job.FAILED, message="The job was interrupted. Please submit it again.", finished_at=now)

        n_jobs = 0
        old = Job.objects.filter(created_at__lt=now - timedelta(days=settings.JOB_MAX_AGE_DAYS)) \
            .exclude(status=Job.EXPIRED)
        for job in old:
            shutil.rmtree(job.directory(), ignore_errors=True)
            job.status = Job.EXPIRED
            job.results = []
            job.save(update_fields=["status", "results"])
            n_jobs += 1
        self.stdout.write(f"{n_stuck} interrupted jobs marked failed; results of {n_jobs} old jobs removed")
