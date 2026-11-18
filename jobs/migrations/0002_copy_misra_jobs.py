"""Copy the jobs of the former misra.Job model (table misra_job) into jobs.Job, keeping ids and slugs.

Job links (/misra/job/<slug>/) and result directories (JOB_DIR/<slug>) stay valid.
misra 0005 removes the old table afterwards.
"""
from django.db import migrations


def copy_jobs(apps, schema_editor):
    Old = apps.get_model("misra", "Job")
    New = apps.get_model("jobs", "Job")
    batch = []
    for old in Old.objects.order_by("pk").iterator():
        batch.append(New(
            id=old.id, slug=old.slug, app="misra", tool=old.tool, status=old.status, params=old.params,
            inputs={"exp_ids": old.exp_ids.split(), "source": old.source or "accessions"},
            n_items=old.n_experiments, results=old.results, message=old.message,
            started_at=old.started_at, finished_at=old.finished_at, created_at=old.created_at,
        ))
    if not batch:
        return
    created = {old.id: old.created_at for old in batch}
    New.objects.bulk_create(batch, batch_size=500)
    # auto_now_add replaced created_at on insert; restore the original times (they drive expiry)
    rows = list(New.objects.filter(pk__in=created))
    for row in rows:
        row.created_at = created[row.pk]
    New.objects.bulk_update(rows, ["created_at"], batch_size=500)


def remove_copies(apps, schema_editor):
    apps.get_model("jobs", "Job").objects.filter(app="misra").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0001_initial"),
        ("misra", "0004_job"),
    ]

    operations = [migrations.RunPython(copy_jobs, remove_copies)]
