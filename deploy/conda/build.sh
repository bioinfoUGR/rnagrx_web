#!/bin/bash
set -euo pipefail
cd /var/www/conda/setup
echo "=== $(date) download Miniforge"
URL=https://github.com/conda-forge/miniforge/releases/download/26.9.4-2/Miniforge3-26.9.4-2-Linux-x86_64.sh
curl -fsSL -o Miniforge3.sh "$URL"
curl -fsSL -o Miniforge3.sh.sha256 "$URL.sha256"
echo "$(cut -d" " -f1 Miniforge3.sh.sha256)  Miniforge3.sh" | sha256sum -c -
echo "=== $(date) install Miniforge"
bash Miniforge3.sh -b -u -p /var/www/conda
/var/www/conda/bin/conda config --file /var/www/conda/.condarc --set channel_priority strict
/var/www/conda/bin/conda --version
echo "=== $(date) create srnatoolbox env"
/var/www/conda/bin/conda env create -y -f srnatoolbox.yml
echo "=== $(date) write lock files"
/var/www/conda/bin/conda list -n srnatoolbox --explicit --md5 > srnatoolbox.lock.txt
/var/www/conda/bin/conda env export -n srnatoolbox --no-builds > srnatoolbox.versions.yml
echo "=== $(date) DONE"
