#!/bin/bash
set -euo pipefail
cd /var/www/conda/setup
C=/var/www/conda/bin/conda
for attempt in 1 2 3; do
  echo "=== $(date) create srnatoolbox env, attempt $attempt"
  if $C env create -y -f srnatoolbox.yml; then ok=1; break; fi
  ok=0; $C env remove -y -n srnatoolbox >/dev/null 2>&1 || true; sleep 30
done
[ "$ok" = 1 ] || { echo "=== FAILED after 3 attempts"; exit 1; }
echo "=== $(date) write lock files"
$C list -n srnatoolbox --explicit --md5 > srnatoolbox.lock.txt
$C env export -n srnatoolbox --no-builds > srnatoolbox.versions.yml
echo "=== $(date) DONE"
