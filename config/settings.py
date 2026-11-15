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
    "misra",
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
if env.bool("DJANGO_SECURE_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
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

# Celery (jobs come in later phases; the app is configured already)
CELERY_BROKER_URL = env.str("REDIS_URL", default="redis://127.0.0.1:6379/2")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = TIME_ZONE

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
