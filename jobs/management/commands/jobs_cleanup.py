import shutil
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from jobs.datasets import active_jobs, delete_dataset
from jobs.models import DataFile, Dataset, Job, Upload

STUCK_AFTER_DAYS = 2


class Command(BaseCommand):
    help = (f"Mark jobs that stayed waiting/running for {STUCK_AFTER_DAYS} days as failed, delete the result "
            "files of jobs older than JOB_MAX_AGE_DAYS days (the job rows stay, marked 'expired'), and delete "
            "uploads not used by a job within UPLOAD_MAX_AGE_HOURS, datasets unused for DATASET_MAX_AGE_DAYS days "
            "and empty datasets after UPLOAD_MAX_AGE_HOURS.")

    def handle(self, *args, **opts):
        now = timezone.now()
        # jobs whose worker died
        n_stuck = Job.objects.filter(status__in=[Job.QUEUED, Job.RUNNING],
                                     created_at__lt=now - timedelta(days=STUCK_AFTER_DAYS)) \
            .update(status=Job.FAILED, message="The job was interrupted. Please submit it again.", finished_at=now)

        n_jobs = 0
        # a job whose results a running job reads (sRNAde on sRNAbench) is kept until that job ends
        old = Job.objects.filter(created_at__lt=now - timedelta(days=settings.JOB_MAX_AGE_DAYS)) \
            .exclude(status=Job.EXPIRED).exclude(children__status__in=[Job.QUEUED, Job.RUNNING])
        for job in old:
            shutil.rmtree(job.directory(), ignore_errors=True)
            job.status = Job.EXPIRED
            job.results = []
            job.save(update_fields=["status", "results"])
            n_jobs += 1
        n_uploads = self.clean_uploads(now)
        n_datasets, n_transfers = self.clean_datasets(now)
        self.stdout.write(f"{n_stuck} interrupted jobs marked failed; results of {n_jobs} old jobs removed; "
                          f"{n_uploads} unused uploads removed; {n_datasets} datasets deleted; "
                          f"{n_transfers} stalled transfers marked failed")

    def clean_datasets(self, now) -> tuple[int, int]:
        # downloads/unpacking whose worker died
        n_transfers = 0
        for df in DataFile.objects.filter(status__in=[DataFile.TRANSFERRING, DataFile.UNPACKING],
                                          updated_at__lt=now - timedelta(days=1)):
            shutil.rmtree(df.incoming(), ignore_errors=True)
            df.status = DataFile.FAILED
            df.message = "The transfer was interrupted. Please add the file again."
            df.save(update_fields=["status", "message", "updated_at"])
            n_transfers += 1
        n = 0
        live = Dataset.objects.filter(deleted_at__isnull=True)
        old = live.filter(last_used_at__lt=now - timedelta(days=settings.DATASET_MAX_AGE_DAYS))
        empty = live.filter(created_at__lt=now - timedelta(hours=settings.UPLOAD_MAX_AGE_HOURS), files__isnull=True,
                            uploads__isnull=True)
        for ds in (old | empty).distinct():
            if not active_jobs(ds).exists():
                delete_dataset(ds)
                n += 1
        return n, n_transfers

    def clean_uploads(self, now) -> int:
        cutoff = now - timedelta(hours=settings.UPLOAD_MAX_AGE_HOURS)
        old = Upload.objects.filter(updated_at__lt=cutoff).exclude(status=Upload.USED)
        n = 0
        for u in old:
            shutil.rmtree(u.directory(), ignore_errors=True)
            u.delete()
            n += 1
        Upload.objects.filter(status=Upload.USED, updated_at__lt=now - timedelta(days=settings.JOB_MAX_AGE_DAYS)) \
            .delete()
        # directories without a row (e.g. a crash between creating the row and the directory)
        known = set(Upload.objects.values_list("token", flat=True))
        root = settings.UPLOAD_DIR
        if root.is_dir():
            for d in root.iterdir():
                if d.name not in known and d.stat().st_mtime < cutoff.timestamp():
                    shutil.rmtree(d, ignore_errors=True)
        return n
