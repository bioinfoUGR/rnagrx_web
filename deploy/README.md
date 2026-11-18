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
| JOB_DIR, JOB_MAX_AGE_DAYS | /scratch/rnagrx/jobs, 14 | job results of all services and their retention (optional; old names MISRA_JOB_DIR, MISRA_JOB_MAX_AGE_DAYS still read) |
| FASTA_UPLOAD_MAX_MB | 50 | size limit of FASTA uploads in tool forms (optional; old name MISRA_UPLOAD_MAX_MB) |
| MISRA_CART_LIMIT | 100000 | optional |
| MISRA_API_MATRIX_LIMIT, MISRA_API_DETAIL_LIMIT | 2000, 1000 | API: experiments per request (matrices / per-experiment details) |
| MISRA_API_RATE_PER_MIN | 120 | API requests per minute and client address (0 = no limit) |

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
../venv/bin/python manage.py test --settings=config.settings_test jobs misra srnatoolbox
```

## gunicorn and Celery under supervisor (root)

Both run as www-data; the files are in `deploy/supervisor/`:

```bash
sudo cp /var/www/rnagrx/app/deploy/supervisor/rnagrx_*.conf /etc/supervisor/conf.d/
sudo supervisorctl reread && sudo supervisorctl update
sudo supervisorctl status rnagrx_web rnagrx_celery
```

After a code update: `sudo supervisorctl restart rnagrx_web rnagrx_celery`.
Job results go to `JOB_DIR` (default `/scratch/rnagrx/jobs`) and are removed by `jobs_cleanup` after
`JOB_MAX_AGE_DAYS` (14). Per-job limits: `MISRA_LIMIT_MATRIX`, `MISRA_LIMIT_DOWNLOAD`, `MISRA_LIMIT_SRNABENCH`.
www-data needs write access to `/var/www/rnagrx/logs` and `/scratch/rnagrx/jobs` (group www-data, mode 2775).

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
50 4 * * *  cd /var/www/rnagrx/app && ../venv/bin/python manage.py jobs_cleanup >> ../logs/jobs_cleanup.log 2>&1
```
