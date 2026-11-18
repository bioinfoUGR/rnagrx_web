# rnagrx web site

Django site of the bioinformatics group of the University of Granada. It hosts **miSRA** (public small-RNA
sequencing experiments processed with sRNAbench) and the new **sRNAtoolbox** (in progress).

- Dev: https://bioinfo5.ugr.es/dev/ (miSRA at `/dev/misra/`, API help at `/dev/misra/api/`)
- Deployment, settings and server layout: [deploy/README.md](deploy/README.md)

## Layout

| Path | Content |
|---|---|
| `config/` | settings (from `/var/www/rnagrx/.env`), URLs, Celery app, DB router for the read-only miSRA database |
| `core/` | landing page, base template, error pages |
| `misradb/` | read-only models of `misradb2` (written by the miSRA processing pipeline) |
| `misra/catalog.py` | the fields of the search index and how they are read from misradb2 |
| `misra/engine.py` | in-memory column index: filtering, facet counts, paging |
| `misra/indexer.py` | `manage.py misra_build_index` (nightly) |
| `misra/views.py`, `query.py`, `cart.py` | browse, taxonomy, detail pages, cart and saved selections |
| `jobs/` | job system shared by the services: Job model, tool registry, Celery task, job page and result files, cleanup |
| `misra/tools.py`, `forms.py`, `runners.py`, `views_tools.py` | miSRA tools (download, matrix, sRNAbench) registered with `jobs` |
| `srnatoolbox/` | sRNAtoolbox at `/srnatoolbox/`: home, about/cite, tool pages (sRNAbench and sRNAde in progress) |
| `misra/api/` | API v1 (`docs.py` describes the endpoints and drives the help page) |
| `misra/expression.py` | reading expression values from misradb2 |
| `deploy/` | gunicorn, supervisor, Apache configs; conda env for srnatoolbox; misra_storage sync |

## Tests

```bash
python manage.py test --settings=config.settings_test jobs misra srnatoolbox
```

The test settings use SQLite and an in-memory cache. `config/settings.py` still reads an env file, so set
`RNAGRX_ENV_FILE` to a file with placeholder values for `DJANGO_SECRET_KEY`, `DB_HOST`, `DB_USER`,
`DB_PASSWORD`, `DB_NAME` and `MISRA_DB_NAME` when running outside the servers.

## Useful commands

```bash
python manage.py misra_build_index        # rebuild the search index from misradb2
python manage.py misra_benchmark          # timing of the index at 300,000 synthetic experiments
python manage.py misra_cleanup            # old carts and sessions
python manage.py jobs_cleanup             # results of old jobs (all services)
```
