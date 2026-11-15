"""FULLTEXT indexes for the free-text search (MariaDB only; other databases skip this)."""
from django.db import migrations


def add_fulltext(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    schema_editor.execute("ALTER TABLE misra_exp_index ADD FULLTEXT INDEX ft_exp_search (search_text)")
    schema_editor.execute("ALTER TABLE misra_study_index ADD FULLTEXT INDEX ft_study_search (title, abstract)")


def drop_fulltext(apps, schema_editor):
    if schema_editor.connection.vendor != "mysql":
        return
    schema_editor.execute("ALTER TABLE misra_exp_index DROP INDEX ft_exp_search")
    schema_editor.execute("ALTER TABLE misra_study_index DROP INDEX ft_study_search")


class Migration(migrations.Migration):
    atomic = False  # MariaDB cannot run DDL inside a transaction
    dependencies = [("misra", "0001_initial")]
    operations = [migrations.RunPython(add_fulltext, drop_fulltext)]
