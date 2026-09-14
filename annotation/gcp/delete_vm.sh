#!/usr/bin/env bash
# Delete an annotator's machine, after their submissions are pulled down.
#
#   annotation/gcp/delete_vm.sh --annotator jamie
#
# Deleting the machine deletes its disk, which is the point: it removes the
# Layer 1 copy that was living on it. Pull the submissions first.
set -euo pipefail

# Settings come from annotation/config.sh -- bucket, zone, ELAN URL, and so on.
# Anything already in your environment wins over it.
CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config.sh"
# shellcheck source=../config.sh
[[ -f "$CONFIG" ]] && source "$CONFIG"

ANNOTATOR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --annotator) ANNOTATOR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[[ -n "$ANNOTATOR" ]] || { echo "usage: delete_vm.sh --annotator NAME" >&2; exit 1; }

echo "This deletes annotate-$ANNOTATOR and its disk."
echo "Have you pulled all of $ANNOTATOR's submissions with pull_submission.py?"
read -r -p "Type the annotator's name to confirm: " CONFIRM
[[ "$CONFIRM" == "$ANNOTATOR" ]] || { echo "Not confirmed; nothing deleted."; exit 1; }

gcloud compute instances delete "annotate-$ANNOTATOR" --zone "$ZONE" --quiet
echo "Deleted."
