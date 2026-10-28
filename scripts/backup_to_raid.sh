#!/usr/bin/env bash
# Nightly backup of this checkout to tofino's external array (/wddata).
#
#   scripts/backup_to_raid.sh        # what cron runs nightly (`crontab -l`)
#
# Layout under $DEST:
#   current/                    rsync mirror of the checkout, .git included
#   changed/<date>/             files a run deleted or overwrote in current/,
#                               so an accidental `rm` here is recoverable
#   manifest/manifest_<date>.sqlite
#                               consistent `sqlite3 .backup` snapshot -- never
#                               a plain copy of the live database
#   LAST_SUCCESS                timestamp of the last complete run
# changed/ and manifest/ keep KEEP_DAYS days. Log: logs/backup.log.
#
# /wddata is NTFS via ntfs-3g: no Unix owners or permission bits (everything
# shows root:root 0777 and chown is a silent no-op), so rsync copies contents,
# symlinks and mtimes (-rlt), not -a.
set -euo pipefail

SRC="$HOME/projects/multidata-local"
DEST="/wddata/multidata-backup"
KEEP_DAYS=60
STAMP=$(date +%F)

mkdir -p "$SRC/logs"
exec >>"$SRC/logs/backup.log" 2>&1
exec 9>/tmp/multidata-backup.lock
flock -n 9 || { echo "$(date -Is) previous backup still running; skipping"; exit 0; }

# fstab mounts /wddata with `nofail`: if the array is absent, /wddata is an
# empty directory on the root SSD and a backup would quietly fill that instead.
mountpoint -q /wddata || { echo "$(date -Is) /wddata not mounted; skipping"; exit 1; }

echo "$(date -Is) start"
mkdir -p "$DEST/current" "$DEST/changed" "$DEST/manifest"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

sqlite3 "$SRC/manifest.sqlite" ".backup $tmp/manifest.sqlite"
check=$(sqlite3 "$tmp/manifest.sqlite" "pragma integrity_check")
[ "$check" = ok ] || { echo "$(date -Is) manifest snapshot failed integrity_check: $check"; exit 1; }
cp "$tmp/manifest.sqlite" "$DEST/manifest/manifest_$STAMP.sqlite"

# The live manifest is covered by the snapshot above; .env holds HF_TOKEN and
# the array is world-writable, so it stays out.
rsync -rlt --delete --stats \
    --backup --backup-dir="$DEST/changed/$STAMP" \
    --exclude='/manifest.sqlite*' --exclude='/.env' \
    --exclude='__pycache__/' --exclude='.pytest_cache/' \
    "$SRC/" "$DEST/current/" >"$tmp/rsync.out"
grep -E 'Number of (regular )?files transferred|Total transferred file size' "$tmp/rsync.out" || true

find "$DEST/manifest" -name 'manifest_*.sqlite' -mtime +"$KEEP_DAYS" -delete
find "$DEST/changed" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

date -Is >"$DEST/LAST_SUCCESS"
echo "$(date -Is) done"
