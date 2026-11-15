# Deploying the rnagrx site

Layout on each server (dev and prod are the same, except `.env`):

```
/var/www/rnagrx/
  .env              settings (not in git), 640 michael:www-data
  app/              this repository
  venv/             Python 3.13 virtualenv (uv), from app/requirements.txt
  python/ tools/    uv-managed Python and uv itself
  static/           collected static files (manage.py collectstatic)
  var/misra_index/  miSRA index arrays, written by misra_build_index
  logs/             gunicorn and job logs
```

## .env keys

| key | example | meaning |
|---|---|---|
| DJANGO_SECRET_KEY | (random) | |
| DJANGO_DEBUG | False | |
| DJANGO_ALLOWED_HOSTS | 192.168.1.81,bioinfo.ugr.es | comma-separated |
| DJANGO_CSRF_TRUSTED_ORIGINS | https://bioinfo.ugr.es | needed for POST behind HTTPS |
| DJANGO_SECURE_PROXY | True | when the front Apache terminates HTTPS and sends X-Forwarded-Proto |
| URL_PREFIX | /dev | path the site lives under; empty on prod |
| DB_HOST, DB_PORT, DB_USER, DB_PASSWORD | | MariaDB account (rnagrx_web) |
| DB_NAME | rnagrx_web | Django's own tables (read/write) |
| MISRA_DB_NAME | misradb2 | miSRA data (read-only) |
| REDIS_URL | redis://127.0.0.1:6379/2 | Celery broker |
| CACHE_URL | redis://127.0.0.1:6379/3 | Django cache (optional, this is the default) |
| MISRA_STORAGE, SCRATCH_DIR, SRNATOOLBOX_DB, SRNATOOLBOX_ENV, ENTREZ_EMAIL | | paths for the services |
| MISRA_CART_LIMIT | 100000 | optional |

## Update the code and the database

```bash
cd /var/www/rnagrx/app
git pull
../venv/bin/python manage.py migrate
../venv/bin/python manage.py collectstatic --noinput
../venv/bin/python manage.py misra_build_index      # after misradb2 changed (also nightly, see below)
sudo supervisorctl restart rnagrx_web              # reload the code
```

A new index build is picked up by the running workers within 30 s; no restart is needed.

## Tests

```bash
../venv/bin/python manage.py test --settings=config.settings_test misra
```

## gunicorn under supervisor (root)

`/etc/supervisor/conf.d/rnagrx_web.conf`:

```ini
[program:rnagrx_web]
command=/var/www/rnagrx/venv/bin/gunicorn -c /var/www/rnagrx/app/deploy/gunicorn/gunicorn.conf.py config.wsgi
directory=/var/www/rnagrx/app
user=www-data
group=www-data
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
```

## Apache (root)

The site prefix is forwarded unchanged; gunicorn strips it (SCRIPT_NAME = URL_PREFIX):

```apache
# dev: replaces the old /dev site
ProxyPreserveHost On
ProxyPass        /dev/ http://127.0.0.1:8100/dev/
ProxyPassReverse /dev/ http://127.0.0.1:8100/dev/
RequestHeader set X-Forwarded-Proto "https" env=HTTPS
```

Static files are served by WhiteNoise from gunicorn; no Apache alias is needed.

## Nightly jobs (crontab of michael)

```
30 3 * * *  cd /var/www/rnagrx/app && ../venv/bin/python manage.py misra_build_index >> ../logs/misra_build_index.log 2>&1
45 4 * * *  cd /var/www/rnagrx/app && ../venv/bin/python manage.py misra_cleanup >> ../logs/misra_cleanup.log 2>&1
```
