"""Django settings for the rnagrx web site.

All machine-specific values come from the .env file one level above the
repository (/var/www/rnagrx/.env); see deploy/README.md for the keys.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent  # .../rnagrx/app
SITE_ROOT = BASE_DIR.parent  # .../rnagrx (venv, .env, logs, var, static)

env = environ.Env()
environ.Env.read_env(env.str("RNAGRX_ENV_FILE", default=str(SITE_ROOT / ".env")))

DEBUG = env.bool("DJANGO_DEBUG", default=False)
SECRET_KEY = env.str("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# The site lives below a path prefix on dev ("/dev"); empty on prod.
# gunicorn gets the same value as SCRIPT_NAME (deploy/gunicorn/gunicorn.conf.py).
URL_PREFIX = env.str("URL_PREFIX", default="").rstrip("/")
FORCE_SCRIPT_NAME = URL_PREFIX or None

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django_htmx",
    "core",
    "misradb",
    "jobs",
    "misra",
    "srnatoolbox",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "misra.context_processors.cart",
            ],
        },
    },
]


def _mysql(name):
    return {
        "ENGINE": "django.db.backends.mysql",
        "NAME": name,
        "HOST": env.str("DB_HOST"),
        "PORT": env.str("DB_PORT", default="3306"),
        "USER": env.str("DB_USER"),
        "PASSWORD": env.str("DB_PASSWORD"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"},
    }


DATABASES = {
    # Django's own tables: sessions, carts, saved selections, the miSRA search index.
    "default": _mysql(env.str("DB_NAME")),
    # miSRA data, written by the processing pipeline. Read-only for the web app.
    "misra": _mysql(env.str("MISRA_DB_NAME")),
}
DATABASE_ROUTERS = ["config.routers.MisraDbRouter"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        # Separate Redis db from the Celery broker (REDIS_URL), so clearing the cache never drops jobs.
        "LOCATION": env.str("CACHE_URL", default="redis://127.0.0.1:6379/3"),
        "KEY_PREFIX": "rnagrx",
        "TIMEOUT": 600,
    }
}

# Own cookie names: the old site on the same host uses Django's default names.
SESSION_COOKIE_NAME = "rnagrx_session"
CSRF_COOKIE_NAME = "rnagrx_csrftoken"
SESSION_COOKIE_PATH = (URL_PREFIX or "") + "/"
CSRF_COOKIE_PATH = SESSION_COOKIE_PATH
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_SAVE_EVERY_REQUEST = False
# Behind Apache: requests that arrived over HTTPS carry X-Forwarded-Proto: https (set by the dev/prod
# Apache only for trusted proxies, see deploy/apache/).
if env.bool("DJANGO_SECURE_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Cookies only over HTTPS: enable once the site is reached only through HTTPS (production).
if env.bool("DJANGO_SECURE_COOKIES", default=False):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/Madrid"
USE_I18N = False
USE_TZ = True

# Absolute, with the prefix: a relative STATIC_URL gets the script prefix only from the
# first request that reads it, which goes wrong with gunicorn's preload_app.
STATIC_URL = f"{URL_PREFIX}/static/"
STATIC_ROOT = Path(env.str("STATIC_ROOT", default=str(SITE_ROOT / "static")))
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# Celery (runs the jobs, see jobs/tasks.py)
CELERY_BROKER_URL = env.str("REDIS_URL", default="redis://127.0.0.1:6379/2")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_DEFAULT_QUEUE = "rnagrx_quick"
CELERY_TASK_IGNORE_RESULT = True  # job state lives in the Job table
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

# Paths used by the services
MISRA_STORAGE = Path(env.str("MISRA_STORAGE", default="/shared/misra_storage"))
SCRATCH_DIR = Path(env.str("SCRATCH_DIR", default="/scratch/rnagrx"))
SRNATOOLBOX_DB = env.str("SRNATOOLBOX_DB", default="")
SRNATOOLBOX_ENV = env.str("SRNATOOLBOX_ENV", default="")
ENTREZ_EMAIL = env.str("ENTREZ_EMAIL", default="")

# miSRA browsing
MISRA_INDEX_DIR = Path(env.str("MISRA_INDEX_DIR", default=str(SITE_ROOT / "var" / "misra_index")))
MISRA_CART_LIMIT = env.int("MISRA_CART_LIMIT", default=100_000)
MISRA_CART_MAX_AGE_DAYS = env.int("MISRA_CART_MAX_AGE_DAYS", default=30)
MISRA_PAGE_SIZE = 50

# Jobs of all services: working directories and retention (the MISRA_* keys are the old names)
JOB_DIR = Path(env.str("JOB_DIR", default=env.str("MISRA_JOB_DIR", default=str(SCRATCH_DIR / "jobs"))))
JOB_MAX_AGE_DAYS = env.int("JOB_MAX_AGE_DAYS", default=env.int("MISRA_JOB_MAX_AGE_DAYS", default=14))
FASTA_UPLOAD_MAX_MB = env.int("FASTA_UPLOAD_MAX_MB", default=env.int("MISRA_UPLOAD_MAX_MB", default=50))
# Read files uploaded in chunks from the browser (jobs/uploads.py) or downloaded from links (jobs/fetch.py)
UPLOAD_DIR = Path(env.str("UPLOAD_DIR", default=str(SCRATCH_DIR / "uploads")))
UPLOAD_MAX_GB = env.int("UPLOAD_MAX_GB", default=10)  # per file (also for links)
UPLOAD_SESSION_MAX_GB = env.int("UPLOAD_SESSION_MAX_GB", default=30)  # uploads of one browser not yet used by a job
UPLOAD_CHUNK_MB = env.int("UPLOAD_CHUNK_MB", default=16)
UPLOAD_MAX_AGE_HOURS = env.int("UPLOAD_MAX_AGE_HOURS", default=48)  # unused uploads are deleted after this
SCRATCH_MIN_FREE_GB = env.int("SCRATCH_MIN_FREE_GB", default=100)  # refuse uploads below this free space

# miSRA tools: max experiments per job, sRNAbench resources
MISRA_LIMIT_MATRIX = env.int("MISRA_LIMIT_MATRIX", default=20_000)
MISRA_LIMIT_DOWNLOAD = env.int("MISRA_LIMIT_DOWNLOAD", default=2_000)
MISRA_LIMIT_SRNABENCH = env.int("MISRA_LIMIT_SRNABENCH", default=500)
MISRA_SRNABENCH_META_THREADS = env.int("MISRA_SRNABENCH_META_THREADS", default=4)  # experiments in parallel
MISRA_SRNABENCH_THREADS = env.int("MISRA_SRNABENCH_THREADS", default=2)  # threads per experiment
MISRA_SRNABENCH_TIMEOUT_HOURS = env.int("MISRA_SRNABENCH_TIMEOUT_HOURS", default=24)
# API: experiments per request for matrices / per-experiment details, requests per minute and IP (0 = no limit)
MISRA_API_MATRIX_LIMIT = env.int("MISRA_API_MATRIX_LIMIT", default=2_000)
MISRA_API_DETAIL_LIMIT = env.int("MISRA_API_DETAIL_LIMIT", default=1_000)
MISRA_API_RATE_PER_MIN = env.int("MISRA_API_RATE_PER_MIN", default=120)
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
