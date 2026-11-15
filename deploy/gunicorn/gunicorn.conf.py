"""gunicorn settings for the rnagrx site.

    /var/www/rnagrx/venv/bin/gunicorn -c /var/www/rnagrx/app/deploy/gunicorn/gunicorn.conf.py config.wsgi

Apache forwards the site's prefix (e.g. /dev/) to 127.0.0.1:8100 unchanged; gunicorn
strips SCRIPT_NAME (= URL_PREFIX from .env) so Django sees /misra/... and builds
links with the prefix.
"""
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parents[3]  # /var/www/rnagrx


def _env_value(name, default=""):
    env_file = SITE_ROOT / ".env"
    try:
        for line in env_file.read_text().splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return default


chdir = str(SITE_ROOT / "app")
bind = _env_value("GUNICORN_BIND", "127.0.0.1:8100")
workers = int(_env_value("GUNICORN_WORKERS", "3"))
worker_class = "gthread"
threads = 4
timeout = 120
graceful_timeout = 30
max_requests = 2000  # recycle workers now and then
max_requests_jitter = 200
preload_app = True  # import Django once (slow on GlusterFS), then fork the workers
raw_env = [f"SCRIPT_NAME={_env_value('URL_PREFIX').rstrip('/')}"]
forwarded_allow_ips = "127.0.0.1"
accesslog = str(SITE_ROOT / "logs" / "gunicorn_access.log")
errorlog = str(SITE_ROOT / "logs" / "gunicorn_error.log")
loglevel = "info"
proc_name = "rnagrx_web"


WARM_TEMPLATES = ["core/landing.html", "misra/home.html", "misra/browse.html", "misra/cart.html",
                  "misra/selection.html", "misra/experiment.html", "misra/study.html", "misra/taxonomy.html",
                  "misra/explore.html", "misra/docs.html", "misra/_toast.html", "misra/_facet_more.html"]


def when_ready(server):
    """Before the workers are forked: load the miSRA index and compile the main templates in
    the master, so every worker starts warm (file access on GlusterFS can be slow) and the
    index arrays are shared copy-on-write instead of loaded once per worker."""
    try:
        from django.db import connections
        from django.template.loader import get_template

        from misra.index_store import get_catalog

        get_catalog()
        for name in WARM_TEMPLATES:
            get_template(name)
        connections.close_all()  # never share DB sockets with forked workers
    except Exception as exc:  # the site must still start without an index
        server.log.warning("warm-up failed: %s", exc)
