import shutil
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone

from misra.models import Cart, Job


class Command(BaseCommand):
    help = ("Delete carts not used for MISRA_CART_MAX_AGE_DAYS days, expired sessions, and the result files "
            "of jobs older than MISRA_JOB_MAX_AGE_DAYS days (the job rows stay, marked 'expired').")

    def handle(self, *args, **opts):
        now = timezone.now()
        n_carts, _ = Cart.objects.filter(updated_at__lt=now - timedelta(days=settings.MISRA_CART_MAX_AGE_DAYS)).delete()
        n_sessions, _ = Session.objects.filter(expire_date__lt=now).delete()

        # jobs whose worker died: never left queued/running for two days
        n_stuck = Job.objects.filter(status__in=[Job.QUEUED, Job.RUNNING], created_at__lt=now - timedelta(days=2)) \
            .update(status=Job.FAILED, message="The job was interrupted. Please submit it again.", finished_at=now)

        n_jobs = 0
        old = Job.objects.filter(created_at__lt=now - timedelta(days=settings.MISRA_JOB_MAX_AGE_DAYS)) \
            .exclude(status=Job.EXPIRED)
        for job in old:
            shutil.rmtree(job.directory(), ignore_errors=True)
            job.status = Job.EXPIRED
            job.results = []
            job.save(update_fields=["status", "results"])
            n_jobs += 1
        self.stdout.write(f"deleted {n_carts} cart objects, {n_sessions} expired sessions; "
                          f"{n_stuck} interrupted jobs marked failed; results of {n_jobs} old jobs removed")
