#!/usr/bin/env bash
# Deploy the Encounter Explorer to Google Cloud Run, with the built bundles
# in a Cloud Storage bucket mounted read-only at /data.
#
#   export GCP_PROJECT=my-project GCP_REGION=us-central1 \
#          GCP_BUCKET=my-project-encounter-explorer GCP_REPO=encounter-explorer
#   demo/deploy_gcp.sh data      # upload data/demo/ (cases + overview) to the bucket
#   demo/deploy_gcp.sh image     # build the app image and push it to Artifact Registry
#   demo/deploy_gcp.sh service   # create/update the Cloud Run service
#   demo/deploy_gcp.sh all       # the three in order
#
# Needs: docker, gsutil, and gcloud (the Google Cloud CLI; it also gives
# docker its Artifact Registry login), authenticated to the project.
# Run from anywhere in the repo. Re-running any step is safe: `data`
# only uploads what changed, `image` pushes a new tag, `service` rolls out
# a new revision.
#
# Access: the service is deployed WITHOUT public access
# (--no-allow-unauthenticated). Grant people roles/run.invoker, or put
# Identity-Aware Proxy in front of it; for a quick private look,
# `gcloud run services proxy $SERVICE --region $GCP_REGION` serves it on
# localhost:8080. See demo/README.md, "Deploying to Google Cloud".
set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${GCP_REGION:?set GCP_REGION (e.g. us-central1)}"
: "${GCP_BUCKET:?set GCP_BUCKET (bucket name, no gs://)}"
GCP_REPO="${GCP_REPO:-encounter-explorer}"
SERVICE="${SERVICE:-encounter-explorer}"
TAG="${TAG:-$(git -C "$(dirname "$0")" rev-parse --short HEAD)$(git -C "$(dirname "$0")" diff --quiet || echo -dirty)}"
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_REPO}/encounter-explorer:${TAG}"

step_data() {
  echo "== uploading bundles to gs://${GCP_BUCKET} (contact sheets stay local)"
  gsutil ls -b "gs://${GCP_BUCKET}" >/dev/null 2>&1 || \
    gsutil mb -p "$GCP_PROJECT" -l "$GCP_REGION" -b on "gs://${GCP_BUCKET}"
  gsutil -m rsync -r -x '^sheets/.*' "$ROOT/data/demo" "gs://${GCP_BUCKET}"
}

step_image() {
  echo "== building and pushing ${IMAGE}"
  gcloud artifacts repositories describe "$GCP_REPO" --project "$GCP_PROJECT" --location "$GCP_REGION" \
    >/dev/null 2>&1 || gcloud artifacts repositories create "$GCP_REPO" --project "$GCP_PROJECT" \
    --location "$GCP_REGION" --repository-format docker --description "Encounter Explorer demo"
  gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet
  DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -f "$ROOT/demo/Dockerfile" --target app -t "$IMAGE" "$ROOT"
  docker push "$IMAGE"
}

step_service() {
  echo "== deploying Cloud Run service ${SERVICE}"
  gcloud run deploy "$SERVICE" \
    --project "$GCP_PROJECT" --region "$GCP_REGION" \
    --image "$IMAGE" \
    --execution-environment gen2 \
    --add-volume "name=bundles,type=cloud-storage,bucket=${GCP_BUCKET},readonly=true" \
    --add-volume-mount "volume=bundles,mount-path=/data" \
    --cpu 1 --memory 1Gi --concurrency 40 --timeout 300 \
    --min-instances 0 --max-instances 3 \
    --no-allow-unauthenticated
  echo "The service account Cloud Run uses needs roles/storage.objectViewer on gs://${GCP_BUCKET}."
  gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$GCP_REGION" --format 'value(status.url)'
}

case "${1:-}" in
  data) step_data ;;
  image) step_image ;;
  service) step_service ;;
  all) step_data; step_image; step_service ;;
  *) sed -n '2,22p' "$0"; exit 1 ;;
esac
