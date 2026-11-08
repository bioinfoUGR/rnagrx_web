# srnatoolbox conda environment

Installed per server under `/var/www/conda` (Miniforge 26.9.4-2, conda-forge + bioconda only).

| File | Purpose |
|------|---------|
| `srnatoolbox.yml` | Hand-written spec (what we ask for) |
| `srnatoolbox.lock.txt` | Exact package URLs + md5 from the dev build (2026-11-08) |
| `srnatoolbox.versions.yml` | Human-readable list of resolved versions |
| `build.sh` / `env.sh` | Scripts used on dev: install Miniforge, create env (with retries) |

To build an **identical** env on another server (e.g. prod):

    /var/www/conda/bin/conda create -n srnatoolbox --file srnatoolbox.lock.txt

The srnatoolbox Python package (sRNAbench, sRNAhelper, ...) is installed on top with pip.
