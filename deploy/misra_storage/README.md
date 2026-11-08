# Updating `/shared/misra_storage`

`/shared/misra_storage` holds the miSRA read files. The paths in `misradb2.read_files.relative_path`
are relative to this folder. The **master copy** is on the storage server:

| | Host | Path |
|---|---|---|
| Source (master) | `192.168.1.194` (`rna`) | `/scratch/mic/misra_storage/` |
| Target | dev `192.168.1.81` (also visible on prod: `/shared` is shared) | `/shared/misra_storage/` |

## When to update
After the processing pipeline has added new experiments to `misradb2`. Database rows can exist
before their files are synced, so the web app must check that a file exists before offering it.

## How to update (from your own computer)

```bash
ssh-add -l                                    # your key must be unlocked
cd <repo>/deploy/misra_storage
./sync_misra_storage.sh --dry-run             # 1. preview: lists files that would be copied
./sync_misra_storage.sh                       # 2. copy
```

- Only new or changed files are copied, so re-running is cheap.
- If it is interrupted (laptop asleep, network drop), run it again and it continues.
- For the **first, large copy**, run it inside `tmux` or `screen`, so closing the terminal does not stop it.
- Each run is logged on dev in `/var/www/rnagrx/logs/misra_storage_sync.log`.

### The same thing by hand
```bash
ssh -A dev rsync -rlt --perms --chmod=D2775,F664 --partial --info=stats2,progress2 \
    michael@192.168.1.194:/scratch/mic/misra_storage/ /shared/misra_storage/
```
Keep the **trailing slashes**. Without the one on the source, rsync would create
`/shared/misra_storage/misra_storage/`.

## What the options do
| Option | Why |
|---|---|
| `-rlt` | recursive, keep symlinks and modification times (the times let later runs skip unchanged files) |
| `--perms --chmod=D2775,F664` | folders `rwxrwsr-x`, files `rw-rw-r--`: readable by the web app, writable by group `www-data` |
| no `-o` / `-g` | files get owner `michael` and group `www-data` (inherited from the folder), not the source server's users |
| `--partial` | keep half-copied large files, so a re-run resumes them |
| no `--delete` | files removed on the source stay on the target. On purpose: a mistake on the source cannot wipe the copy |

## Removing files that were deleted on the source
Only if you really want an exact mirror. Always preview first:
```bash
ssh -A dev rsync -rlt --delete --dry-run --itemize-changes \
    michael@192.168.1.194:/scratch/mic/misra_storage/ /shared/misra_storage/ | grep '^\*deleting'
```
If the list is what you expect, run the same command without `--dry-run`.

## Requirements (set up 2026-11-08)
- Your key is in `~/.ssh/authorized_keys` on dev and on 192.168.1.194.
- Dev knows the host key of 192.168.1.194 (in `~/.ssh/known_hosts` on dev).
- `/shared/misra_storage` exists: owner `michael`, group `www-data`, mode `2775`.
