#!/usr/bin/env bash
# One-time project setup. Run once, before the first build.
#
#   export ANNOTATION_BUCKET=gs://som-anno-data-bucket
#   annotation/gcp/setup_project.sh
#
# Every step checks first and skips if already done, so re-running is safe.
#
# What this sets up, and why each one exists:
#
#   1. A firewall rule letting Google's tunnel service reach the machines.
#      Without it, `connect.sh` and `gcloud compute ssh` cannot get in at all.
#   2. Private Google Access on the subnet. This is what lets an annotator
#      machine with NO public IP still reach Cloud Storage -- so it can fetch
#      its kit and upload its work while being unreachable from the internet.
#   3. The bucket, with versioning on, so a backup copy that races an ELAN save
#      is recoverable.
#   4. A service account for the annotator machines.
set -euo pipefail

BUCKET="${ANNOTATION_BUCKET:?set ANNOTATION_BUCKET=gs://your-bucket}"
REGION="${REGION:-us-east5}"
SUBNET="${SUBNET:-default}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"
SA_NAME="annotator"
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

[[ -n "$PROJECT" ]] || { echo "no project set: gcloud config set project ..." >&2; exit 1; }
echo "project: $PROJECT   region: $REGION   bucket: $BUCKET"
echo

# --- 1. let the tunnel in -----------------------------------------------------
# 35.235.240.0/20 is the fixed range Identity-Aware Proxy forwards from. This
# rule is what makes a machine with no public IP reachable, and only from there:
# port 22 for admin SSH, 5901 for the annotator's desktop.
if gcloud compute firewall-rules describe allow-iap-tunnel >/dev/null 2>&1; then
  echo "==> firewall rule allow-iap-tunnel already exists"
else
  echo "==> creating firewall rule allow-iap-tunnel"
  gcloud compute firewall-rules create allow-iap-tunnel \
    --network default \
    --direction INGRESS \
    --action allow \
    --rules tcp:22,tcp:5901 \
    --source-ranges 35.235.240.0/20 \
    --description "IAP tunnel only: admin SSH and the annotation desktop"
fi

# --- 2. reach Cloud Storage without a public IP -------------------------------
echo "==> enabling Private Google Access on subnet $SUBNET/$REGION"
gcloud compute networks subnets update "$SUBNET" \
  --region "$REGION" --enable-private-ip-google-access

# --- 3. the bucket ------------------------------------------------------------
if gcloud storage buckets describe "$BUCKET" >/dev/null 2>&1; then
  echo "==> bucket $BUCKET already exists"
else
  echo "==> creating bucket $BUCKET in $REGION"
  gcloud storage buckets create "$BUCKET" \
    --location "$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
gcloud storage buckets update "$BUCKET" --versioning

# --- 4. the annotator machines' identity --------------------------------------
if gcloud iam service-accounts describe "$SA" >/dev/null 2>&1; then
  echo "==> service account $SA already exists"
else
  echo "==> creating service account $SA"
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name "Annotation machines"
fi

echo "==> granting it access to $BUCKET (and nothing else)"
# Scoped to this bucket rather than the project. One account shared by the
# annotator machines: what keeps one annotator from seeing another's work is
# that only their own case is staged on their own machine, not this binding.
# If that posture ever needs to be stronger, the move is one service account per
# annotator with an IAM condition on resource.name.startsWith(<their prefix>).
gcloud storage buckets add-iam-policy-binding "$BUCKET" \
  --member "serviceAccount:${SA}" \
  --role roles/storage.objectAdmin

cat <<EOF

Done. Next:

  annotation/gcp/build_image.sh v1

If that fails with a policy error about external IP addresses, your
organisation forbids them on VMs. The builder needs the open internet to
install packages, so set up Cloud NAT instead and put --no-address back in
build_image.sh:

  gcloud compute routers create annotation-router \\
    --network default --region $REGION
  gcloud compute routers nats create annotation-nat \\
    --router annotation-router --region $REGION \\
    --auto-allocate-nat-external-ips \\
    --nat-all-subnet-ip-ranges

That costs roughly \$32/month for the gateway, so delete it when the image is
built if you are not using it for anything else:

  gcloud compute routers delete annotation-router --region $REGION --quiet
EOF
