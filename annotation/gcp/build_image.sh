#!/usr/bin/env bash
# Build the annotator machine image. Run once per image version.
#
# Boots a plain Debian VM, runs image/install.sh on it, snapshots the disk as
# an image, and deletes the VM. The result is `annotator-vN`, which every
# annotator machine is then created from.
#
#   annotation/gcp/build_image.sh v1
#
# Rebuild rather than patch: if ELAN needs upgrading or a setting changes,
# build v2 and make new machines from it. Never edit a running annotator's
# machine mid-case -- that changes the tool underneath work in progress.
set -euo pipefail

VERSION="${1:?usage: build_image.sh <version>, e.g. v1}"
ZONE="${ZONE:-us-east5-a}"
BUILDER="annotator-image-builder"
IMAGE="annotator-${VERSION}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"      # annotation/
REPO="$(cd "$HERE/.." && pwd)"

echo "==> booting builder VM in $ZONE"
gcloud compute instances create "$BUILDER" \
  --zone "$ZONE" \
  --machine-type e2-standard-4 \
  --image-family debian-12 --image-project debian-cloud \
  --boot-disk-size 50GB --boot-disk-type pd-balanced \
  --no-address   # no public IP anywhere in this design; IAP carries SSH

echo "==> waiting for SSH"
until gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
        --command true 2>/dev/null; do sleep 10; done

echo "==> staging the image tree (with the guide docs alongside)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$HERE/image" "$STAGE/image"
cp "$HERE/submit_checks.py" "$STAGE/"
mkdir -p "$STAGE/image/docs"
cp "$REPO/docs/annotator_guide.md" "$REPO/docs/transcription_standards.md" \
   "$STAGE/image/docs/"

echo "==> copying it over"
gcloud compute scp --recurse "$STAGE/image" "$STAGE/submit_checks.py" \
  "$BUILDER":/tmp/ --zone "$ZONE" --tunnel-through-iap

echo "==> installing (this takes a while)"
gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
  --command "sudo bash /tmp/image/install.sh"

echo "==> stopping and snapshotting"
gcloud compute instances stop "$BUILDER" --zone "$ZONE"
gcloud compute images create "$IMAGE" \
  --source-disk "$BUILDER" --source-disk-zone "$ZONE" \
  --description "ELAN annotation desktop, $(date +%Y-%m-%d)"

echo "==> deleting the builder"
gcloud compute instances delete "$BUILDER" --zone "$ZONE" --quiet

cat <<EOF

Image $IMAGE is ready.

Record alongside it: the ELAN version installed, today's date, and anything
you changed in image/elan_prefs. Then make an annotator machine:

  annotation/gcp/new_vm.sh --annotator jamie --image $IMAGE
EOF
