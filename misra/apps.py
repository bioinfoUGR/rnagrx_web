from django.apps import AppConfig


class MisraConfig(AppConfig):
    name = "misra"
    verbose_name = "miSRA"

    def ready(self):
        from . import tools  # noqa: F401  registers the miSRA tools with jobs.registry
