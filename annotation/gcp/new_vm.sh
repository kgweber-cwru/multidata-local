#!/usr/bin/env bash
# Create one annotator's machine. One machine per annotator, kept for the
# length of the campaign, with cases staged onto it as they finish.
#
#   annotation/gcp/new_vm.sh --annotator jamie [--image annotator-v1]
#
# The machine gets no public IP, and needs none: everything it runs is baked
# into the image, and the only thing it talks to is Cloud Storage, which Private
# Google Access reaches without an external address (see setup_project.sh). So
# the machine holding the recordings can reach Storage and nothing else, and is
# unreachable from the internet. Annotators get in through connect.sh.
#
# Run setup_project.sh once before the first of these.
set -euo pipefail

# Settings come from annotation/config.sh -- bucket, zone, ELAN URL, and so on.
# Anything already in your environment wins over it.
CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config.sh"
# shellcheck source=../config.sh
[[ -f "$CONFIG" ]] && source "$CONFIG"

ANNOTATOR=""
IMAGE="$ANNOTATOR_IMAGE"
BUCKET="${ANNOTATION_BUCKET:?Not set, and annotation/config.sh was not found or does not define it. config.sh is where these live}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --annotator) ANNOTATOR="$2"; shift 2 ;;
    --image)     IMAGE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[[ -n "$ANNOTATOR" ]] || { echo "usage: new_vm.sh --annotator NAME" >&2; exit 1; }

VM="annotate-${ANNOTATOR}"
SA="${ANNOTATOR_SA:-annotator@$(gcloud config get-value project 2>/dev/null).iam.gserviceaccount.com}"

gcloud compute instances create "$VM" \
  --zone "$ZONE" \
  --machine-type e2-custom-2-5632 \
  --image "$IMAGE" \
  --boot-disk-size 50GB --boot-disk-type pd-balanced \
  --no-address \
  --service-account "$SA" \
  --scopes https://www.googleapis.com/auth/devstorage.read_write \
  --metadata "annotator=${ANNOTATOR},bucket=${BUCKET}"

cat <<EOF

$VM is up.

Grant $ANNOTATOR access to it (once):

  gcloud compute instances add-iam-policy-binding $VM --zone $ZONE \\
    --member "user:${ANNOTATOR}@case.edu" --role roles/compute.osLogin
  gcloud projects add-iam-policy-binding \$(gcloud config get-value project) \\
    --member "user:${ANNOTATOR}@case.edu" --role roles/iap.tunnelResourceAccessor

Then send them annotation/gcp/connect.sh, the VNC password, and this exact
line to run -- their laptop username is probably not "$ANNOTATOR", so the
--annotator flag matters:

  ./connect.sh --annotator $ANNOTATOR --project $(gcloud config get-value project 2>/dev/null)

Walk them through it once; it takes about fifteen minutes.
EOF
