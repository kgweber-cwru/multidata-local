#!/usr/bin/env bash
# Push a kit out to the bucket and stage it on the annotator's machine.
#
#   annotation/gcp/push_kit.sh --case 261473 --annotator jamie
#
# This is the one place the network boundary is crossed, and it is crossed
# outward, by hand, on purpose (annotation design §2). The private network
# cannot be reached from outside, and nothing here tries to change that.
set -euo pipefail

CASE=""; ANNOTATOR=""
ZONE="${ZONE:-us-east5-a}"
BUCKET="${ANNOTATION_BUCKET:?set ANNOTATION_BUCKET=gs://your-bucket}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --case)      CASE="$2"; shift 2 ;;
    --annotator) ANNOTATOR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[[ -n "$CASE" && -n "$ANNOTATOR" ]] || {
  echo "usage: push_kit.sh --case ID --annotator NAME" >&2; exit 1; }

KIT="$REPO/annotation/kits/$CASE/$ANNOTATOR"
[[ -d "$KIT" ]] || { echo "no kit at $KIT -- run make_kit.py first" >&2; exit 1; }

echo "==> uploading $(du -sh "$KIT" | cut -f1) to the bucket"
gcloud storage rsync --recursive "$KIT" "$BUCKET/kits/$CASE/$ANNOTATOR"

echo "==> staging it on annotate-$ANNOTATOR"
gcloud compute ssh "annotate-$ANNOTATOR" --zone "$ZONE" --tunnel-through-iap \
  --command "sudo /usr/local/bin/stage-case $CASE"

echo
echo "Done. Tell $ANNOTATOR that case $CASE is on their machine."
