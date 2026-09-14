#!/usr/bin/env bash
# Create one annotator's machine. One machine per annotator, kept for the
# length of the campaign, with cases staged onto it as they finish.
#
#   annotation/gcp/new_vm.sh --annotator jamie [--image annotator-v1]
#
# The machine gets no public IP. Annotators reach it through connect.sh.
set -euo pipefail

ANNOTATOR=""; IMAGE="${ANNOTATOR_IMAGE:-annotator-v1}"
ZONE="${ZONE:-us-east5-a}"
BUCKET="${ANNOTATION_BUCKET:?set ANNOTATION_BUCKET=gs://your-bucket}"

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
  --machine-type e2-standard-4 \
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

Then send them annotation/gcp/connect.sh and walk them through it once.
EOF
