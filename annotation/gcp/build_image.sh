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

# Check the ELAN settings HERE, before booting a VM. install.sh needs them, but
# it runs on the builder -- and a missing value should cost a second locally,
# not a VM boot and a package install first.
ELAN_VERSION="${ELAN_VERSION:-7.1}"
if [[ -z "${ELAN_DEB_URL:-}" ]]; then
  cat >&2 <<'EOF'
ELAN_DEB_URL is not set. Set it to the ELAN .deb download URL, e.g.

  export ELAN_DEB_URL=https://www.mpi.nl/tools/elan/ELAN_7-1_linux.deb
  export ELAN_DEB_SHA256=...        # optional the first time; see below

ELAN_DEB_SHA256 pins the exact artifact, so a later rebuild installs the same
ELAN rather than whatever is behind that URL by then (standards §12). If you
don't have a published checksum, leave it unset: the build prints the one it
downloaded, and you set it for next time.
EOF
  exit 1
fi
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
# The vars have to be named explicitly twice over: `gcloud compute ssh` starts a
# fresh shell on the builder, so nothing from this shell's environment arrives,
# and `sudo` resets the environment again on top of that. `sudo VAR=... cmd` is
# what gets a value through both.
gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
  --command "sudo \
    ELAN_VERSION='$ELAN_VERSION' \
    ELAN_DEB_URL='$ELAN_DEB_URL' \
    ELAN_DEB_SHA256='${ELAN_DEB_SHA256:-}' \
    bash /tmp/image/install.sh"

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
