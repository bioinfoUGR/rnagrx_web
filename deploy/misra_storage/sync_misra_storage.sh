#!/bin/bash
# Copy new/changed miSRA read files from the storage server to /shared/misra_storage.
#
# RUN THIS ON YOUR OWN COMPUTER (not on dev), with your SSH key unlocked:
#     ssh-add -l                                   # must list your key
#     ./sync_misra_storage.sh --dry-run            # show what would be copied
#     ./sync_misra_storage.sh                      # copy
#
# Data flows directly 192.168.1.194 -> dev; your computer only holds the connection.
# Your key is forwarded to dev for this one connection (ssh -A), so dev needs no key of its own.
# Nothing is ever deleted, neither on the source nor on the target.
# Safe to interrupt and re-run: it continues where it stopped.

set -euo pipefail

SRC="michael@192.168.1.194:/scratch/mic/misra_storage/"   # trailing slash: copy the CONTENTS
DST_HOST="dev"
DST_DIR="/shared/misra_storage/"
LOG="/var/www/rnagrx/logs/misra_storage_sync.log"

DRY=""
if [ "${1:-}" = "--dry-run" ]; then DRY="--dry-run"; fi

if ! ssh-add -l >/dev/null 2>&1; then
    echo "SSH key not unlocked. Run: ssh-add" >&2
    exit 1
fi

# The remote script is sent on stdin, so no nested quoting is needed.
ssh -A "$DST_HOST" bash -s -- "$SRC" "$DST_DIR" "$LOG" "$DRY" <<'REMOTE'
set -uo pipefail
SRC=$1; DST=$2; LOG=$3; DRY=${4:-}
mkdir -p "$(dirname "$LOG")"
{
    echo "=== $(date '+%F %T') sync start ${DRY}"
    rsync -rlt --perms --chmod=D2775,F664 --partial --info=stats2,progress2 $DRY "$SRC" "$DST"
    rc=$?
    echo "=== $(date '+%F %T') sync end (exit $rc)"
    exit $rc
} 2>&1 | tee -a "$LOG"
REMOTE
