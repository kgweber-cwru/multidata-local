#!/usr/bin/env bash
# Deploy the Encounter Explorer to Google Cloud Run, with the built bundles
# in a Cloud Storage bucket mounted read-only at /data, behind
# Identity-Aware Proxy (Google sign-in; only people you grant get in).
#
#   export GCP_PROJECT=my-project GCP_REGION=us-central1 \
#          GCP_BUCKET=my-project-encounter-explorer GCP_REPO=encounter-explorer
#   demo/deploy_gcp.sh data      # upload data/demo/ (cases + overview) to the bucket
#   demo/deploy_gcp.sh image     # build the app image and push it to Artifact Registry
#   demo/deploy_gcp.sh service   # create/update the Cloud Run service, with IAP on
#   demo/deploy_gcp.sh grant a@umich.edu b@umich.edu ...   # let these people in
#   demo/deploy_gcp.sh revoke a@umich.edu                  # take someone out
#   demo/deploy_gcp.sh viewers   # who can get in now
#   demo/deploy_gcp.sh all       # data, image, service in order (then grant)
#
# Needs: docker, gsutil, and gcloud (the Google Cloud CLI; it also gives
# docker its Artifact Registry login), authenticated to the project.
# Run from anywhere in the repo. Re-running any step is safe: `data`
# only uploads what changed, `image` pushes a new tag, `service` rolls out
# a new revision of the newest pushed image (or IMAGE=... if set).
#
# Access: the service is deployed without public access and with IAP on.
# Viewers open the service URL, sign in with their Google account, and get
# in only if `grant` added them (role roles/iap.httpsResourceAccessor). IAP
# on Cloud Run is fairly new: if a gcloud flag here is refused, check
# https://cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run
# See demo/README.md, "Deploying to Google Cloud".
set -euo pipefail

: "${GCP_PROJECT:?set GCP_PROJECT}"
: "${GCP_REGION:?set GCP_REGION (e.g. us-central1)}"
GCP_REPO="${GCP_REPO:-encounter-explorer}"
SERVICE="${SERVICE:-encounter-explorer}"
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
REPO_PATH="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${GCP_REPO}/encounter-explorer"
LOC=(--project "$GCP_PROJECT" --region "$GCP_REGION")

step_data() {
  : "${GCP_BUCKET:?set GCP_BUCKET (bucket name, no gs://)}"
  echo "== uploading bundles to gs://${GCP_BUCKET} (contact sheets stay local)"
  gsutil ls -b "gs://${GCP_BUCKET}" >/dev/null 2>&1 || \
    gsutil mb -p "$GCP_PROJECT" -l "$GCP_REGION" -b on "gs://${GCP_BUCKET}"
  gsutil -m rsync -r -x '^sheets/.*' "$ROOT/data/demo" "gs://${GCP_BUCKET}"
}

step_image() {
  local tag image
  tag="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet || echo -dirty)}"
  image="${REPO_PATH}:${tag}"
  echo "== building and pushing ${image}"
  gcloud artifacts repositories describe "$GCP_REPO" --project "$GCP_PROJECT" --location "$GCP_REGION" \
    >/dev/null 2>&1 || gcloud artifacts repositories create "$GCP_REPO" --project "$GCP_PROJECT" \
    --location "$GCP_REGION" --repository-format docker --description "Encounter Explorer demo"
  gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet
  DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -f "$ROOT/demo/Dockerfile" --target app -t "$image" "$ROOT"
  docker push "$image"
}

latest_image() {
  # the newest image pushed to the repository, by digest
  local ref
  ref="$(gcloud artifacts docker images list "$REPO_PATH" --sort-by=~UPDATE_TIME --limit=1 \
         --format='value(package,version)' | awk '{print $1 "@" $2}')"
  [ -n "$ref" ] || { echo "no image in $REPO_PATH yet: run '$0 image' first" >&2; exit 1; }
  echo "$ref"
}

step_service() {
  : "${GCP_BUCKET:?set GCP_BUCKET (bucket name, no gs://)}"
  local image="${IMAGE:-$(latest_image)}" number
  echo "== deploying Cloud Run service ${SERVICE} from ${image}"
  gcloud services enable iap.googleapis.com --project "$GCP_PROJECT"
  gcloud run deploy "$SERVICE" "${LOC[@]}" \
    --image "$image" \
    --execution-environment gen2 \
    --add-volume "name=bundles,type=cloud-storage,bucket=${GCP_BUCKET},readonly=true" \
    --add-volume-mount "volume=bundles,mount-path=/data" \
    --cpu 1 --memory 1Gi --concurrency 40 --timeout 300 \
    --min-instances 0 --max-instances 3 \
    --no-allow-unauthenticated
  echo "== turning on IAP"
  gcloud beta run services update "$SERVICE" "${LOC[@]}" --iap
  # IAP calls the service as its own service agent, which needs run.invoker
  number="$(gcloud projects describe "$GCP_PROJECT" --format 'value(projectNumber)')"
  gcloud run services add-iam-policy-binding "$SERVICE" "${LOC[@]}" \
    --member "serviceAccount:service-${number}@gcp-sa-iap.iam.gserviceaccount.com" \
    --role roles/run.invoker >/dev/null
  echo "The service's runtime service account needs roles/storage.objectViewer on gs://${GCP_BUCKET}."
  echo "Service URL (viewers sign in here once granted):"
  gcloud run services describe "$SERVICE" "${LOC[@]}" --format 'value(status.url)'
}

iap_binding() {     # add|remove email
  gcloud iap web "$1-iam-policy-binding" "${LOC[@]}" --resource-type cloud-run --service "$SERVICE" \
    --member "user:$2" --role roles/iap.httpsResourceAccessor >/dev/null
}

step_grant() {
  [ $# -gt 0 ] || { echo "usage: $0 grant email [email ...]" >&2; exit 1; }
  for e in "$@"; do iap_binding add "$e" && echo "granted: $e"; done
  step_viewers
}

step_revoke() {
  [ $# -gt 0 ] || { echo "usage: $0 revoke email [email ...]" >&2; exit 1; }
  for e in "$@"; do iap_binding remove "$e" && echo "revoked: $e"; done
  step_viewers
}

step_viewers() {
  echo "== who can get in (roles/iap.httpsResourceAccessor):"
  gcloud iap web get-iam-policy "${LOC[@]}" --resource-type cloud-run --service "$SERVICE" \
    --flatten 'bindings[].members' --filter 'bindings.role=roles/iap.httpsResourceAccessor' \
    --format 'value(bindings.members)'
}

cmd="${1:-}"; shift || true
case "$cmd" in
  data) step_data ;;
  image) step_image ;;
  service) step_service ;;
  grant) step_grant "$@" ;;
  revoke) step_revoke "$@" ;;
  viewers) step_viewers ;;
  all) step_data; step_image; step_service ;;
  *) sed -n '2,27p' "$0"; exit 1 ;;
esac
