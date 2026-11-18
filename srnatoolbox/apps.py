from django.apps import AppConfig


class SrnatoolboxConfig(AppConfig):
    name = "srnatoolbox"
    verbose_name = "sRNAtoolbox"

    def ready(self):
        from . import tools  # noqa: F401  registers the sRNAtoolbox tools with jobs.registry
