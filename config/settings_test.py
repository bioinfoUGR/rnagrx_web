"""Settings for the test suite: SQLite and an in-memory cache, no external services.

Run:  python manage.py test --settings=config.settings_test
"""
import tempfile
from pathlib import Path

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "misra": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
MISRA_INDEX_DIR = Path(tempfile.mkdtemp(prefix="misra_index_test_"))
FORCE_SCRIPT_NAME = None
URL_PREFIX = ""
STATIC_URL = "/static/"
SESSION_COOKIE_PATH = "/"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
