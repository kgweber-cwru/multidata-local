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

# Settings come from annotation/config.sh -- bucket, zone, ELAN URL, and so on.
# Anything already in your environment wins over it.
CONFIG="$(cd "$(dirname "$0")/.." && pwd)/config.sh"
# shellcheck source=../config.sh
[[ -f "$CONFIG" ]] && source "$CONFIG"

# Retry a command a few times. Everything here goes through the IAP tunnel to a
# VM that has just booted, and that path comes up raggedly: sshd, the guest
# agent propagating keys, and the tunnel backend each become ready at their own
# pace, so a step can fail once and work ten seconds later. Retrying the
# transport is the difference between a build that works and a build you run
# four times.
retry() {
  local tries="$1"; shift
  local n=1
  while true; do
    if "$@"; then return 0; fi
    if (( n >= tries )); then
      echo "    gave up after $n attempts" >&2
      return 1
    fi
    echo "    attempt $n failed; retrying in 15s" >&2
    sleep 15
    (( n++ ))
  done
}


VERSION="${1:?usage: build_image.sh <version> [--reuse]   e.g. v1}"
REUSE=no
[[ "${2:-}" == "--reuse" ]] && REUSE=yes
# Check the ELAN settings HERE, before booting a VM. install.sh needs them, but
# it runs on the builder -- and a missing value should cost a second locally,
# not a VM boot and a package install first.
if [[ -z "${ELAN_DEB_URL:-}" ]]; then
  cat >&2 <<'EOF'
ELAN_DEB_URL is not set, and annotation/config.sh was not found or does not
define it. That file is where it lives -- check you are running this from the
repo, or set it for one run:

  ELAN_DEB_URL=https://www.mpi.nl/tools/elan/ELAN_7-1_linux.deb \
    annotation/gcp/build_image.sh v1

ELAN_DEB_SHA256 pins the exact artifact, so a later rebuild installs the same
ELAN rather than whatever is behind that URL by then (standards §12). It is
optional: leave it unset and the build prints the checksum it downloaded.
EOF
  exit 1
fi
BUILDER="annotator-image-builder"
IMAGE="annotator-${VERSION}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"      # annotation/
REPO="$(cd "$HERE/.." && pwd)"

# --reuse keeps the builder from a failed run and just re-runs the install on
# it. Booting a VM and installing several hundred packages is most of the wall
# clock here, and when you are fixing install.sh a line at a time you do not
# want to pay for that each round. Use it while iterating; do the final build
# without it, so the image you ship comes from a clean machine.
if [[ "$REUSE" == "yes" ]]; then
  if ! gcloud compute instances describe "$BUILDER" --zone "$ZONE" >/dev/null 2>&1; then
    echo "--reuse needs an existing $BUILDER, and there isn't one." >&2
    echo "Run without --reuse to boot a fresh builder." >&2
    exit 1
  fi
  echo "==> reusing the existing builder (packages already installed)"
  if ! gcloud compute instances describe "$BUILDER" --zone "$ZONE" \
        --format='value(status)' | grep -q RUNNING; then
    echo "    it is not running; starting it"
    gcloud compute instances start "$BUILDER" --zone "$ZONE"
  fi
  echo "    waiting for SSH"
  retry 20 gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
    --command true >/dev/null 2>&1 || {
      echo "cannot reach $BUILDER over SSH." >&2
      exit 1
    }
elif gcloud compute instances describe "$BUILDER" --zone "$ZONE" >/dev/null 2>&1; then
  # A failed run leaves the builder behind. Clear it out rather than making a
  # retry the thing that has to notice.
  echo "==> removing a leftover builder from a previous run"
  gcloud compute instances delete "$BUILDER" --zone "$ZONE" --quiet
fi

# An image of this name would only fail at the very end, after the whole build.
# Find out now.
if gcloud compute images describe "$IMAGE" >/dev/null 2>&1; then
  # Suggest the next version number when it's a plain vN, and stay quiet about
  # it when it isn't -- a failed arithmetic expansion here would replace the
  # helpful message with a confusing one.
  NEXT="the next version"
  if [[ "$VERSION" =~ ^v([0-9]+)$ ]]; then
    NEXT="v$(( ${BASH_REMATCH[1]} + 1 ))"
  fi
  cat >&2 <<EOF
Image $IMAGE already exists, and this would fail at the last step.

Either build $NEXT:
  annotation/gcp/build_image.sh $NEXT

or delete this one first -- safe only if no annotator machine was made from it:
  gcloud compute images delete $IMAGE --quiet
EOF
  exit 1
fi

# On failure, say where things stand. Deliberately does NOT delete the builder:
# a build that got far enough to fail interestingly is worth logging into, and
# the next run cleans it up anyway.
cleanup() {
  local code=$?
  [[ -n "${STAGE:-}" ]] && rm -rf "$STAGE"
  [[ $code -eq 0 ]] && return 0

  echo >&2
  echo "Build failed (exit $code)." >&2
  if gcloud compute instances describe "$BUILDER" --zone "$ZONE" >/dev/null 2>&1; then
    cat >&2 <<EOF
The builder VM is still up, on purpose, so you can see what happened:

  gcloud compute ssh $BUILDER --zone $ZONE --tunnel-through-iap
  sudo bash /tmp/image/install.sh     # run the install by hand and watch it fail

Re-running build_image.sh deletes it and starts clean, so there is nothing to
tidy up first. To remove it now anyway:

  gcloud compute instances delete $BUILDER --zone $ZONE --quiet
EOF
  else
    echo "No builder VM was created, so there is nothing to clean up." >&2
  fi
}
trap cleanup EXIT

if [[ "$REUSE" == "no" ]]; then
  echo "==> booting builder VM in $ZONE"
  # The builder is the ONE machine here that gets a public IP, and it needs one:
  # it installs Debian packages and downloads ELAN, so it needs the open internet,
  # and a VM with no external address and no NAT has no outbound route at all.
  #
  # Annotator machines are the opposite and keep --no-address (see new_vm.sh).
  # They never install anything -- it's all baked into the image -- and the only
  # thing they talk to is Google Cloud Storage, which Private Google Access
  # reaches without an external IP. So the machines that hold the recordings can
  # reach Storage and nothing else, while the throwaway builder is the only thing
  # that ever touches the open internet. That's a better split than giving
  # everything NAT.
  #
  # The address is ephemeral and lives only for this build. If your organisation
  # forbids external IPs on VMs, creation fails with a policy error -- see
  # setup_project.sh for the Cloud NAT alternative.
  gcloud compute instances create "$BUILDER" \
    --zone "$ZONE" \
    --machine-type e2-custom-2-5632 \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 50GB --boot-disk-type pd-balanced

  # Wait for SSH to be *stable*, not merely to have worked once. One success
  # right after boot proves nothing -- the previous version of this check
  # passed and then the very next step failed on port 22. Require three
  # consecutive successes, and give up rather than hanging forever.
  echo "==> waiting for SSH to settle"
  OK=0
  DEADLINE=$(( $(date +%s) + 300 ))
  while (( OK < 3 )); do
    if (( $(date +%s) > DEADLINE )); then
      echo "SSH to $BUILDER never settled within 5 minutes." >&2
      echo "Last attempt, with its error:" >&2
      gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
        --command true >&2 || true
      exit 1
    fi
    if gcloud compute ssh "$BUILDER" --zone "$ZONE" --tunnel-through-iap \
         --command true >/dev/null 2>&1; then
      (( OK++ ))
    else
      OK=0
      sleep 10
    fi
  done
  echo "    ready"
fi

echo "==> staging the image tree (with the guide docs alongside)"
STAGE="$(mktemp -d)"
cp -R "$HERE/image" "$STAGE/image"
cp "$HERE/submit_checks.py" "$STAGE/"

# The settings install.sh needs, as a file that travels with the tree. This
# replaces passing values through `gcloud compute ssh` and then `sudo`, neither
# of which carries an environment -- the cause of several failed builds, and of
# install.sh being impossible to run by hand.
{
  echo "# Written by build_image.sh $(date -u +%Y-%m-%dT%H:%M:%SZ). Not tracked."
  echo "ELAN_VERSION=\"$ELAN_VERSION\""
  echo "ELAN_DEB_URL=\"$ELAN_DEB_URL\""
  echo "ELAN_DEB_SHA256=\"${ELAN_DEB_SHA256:-}\""
  if [[ -n "${DESKTOP_PASSWORD:-${VNC_PASSWORD:-}}" ]]; then
    echo "DESKTOP_PASSWORD=\"${DESKTOP_PASSWORD:-${VNC_PASSWORD}}\""
  fi
} > "$STAGE/image/build.env"
mkdir -p "$STAGE/image/docs"
cp "$REPO/docs/annotator_guide.md" "$REPO/docs/transcription_standards.md" \
   "$STAGE/image/docs/"

echo "==> copying it over"
retry 3 gcloud compute scp --recurse "$STAGE/image" "$STAGE/submit_checks.py" \
  "$BUILDER":/tmp/ --zone "$ZONE" --tunnel-through-iap

echo "==> installing (this takes a while)"
# Nothing to pass: install.sh reads /tmp/image/build.env, staged above.
#
# Not wrapped in retry, though install.sh is now safe to re-run: a failing
# install should stop and be looked at, not be attempted three times.
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

Image $IMAGE is ready.$( [[ "$REUSE" == "yes" ]] && printf '\n\n  NOTE: built with --reuse, on a VM that had already had a failed run.\n  Fine for testing. Rebuild without --reuse before annotators use it.' )

Record alongside it: the ELAN version installed, today's date, and anything
you changed in image/elan_prefs. Then make an annotator machine:

  annotation/gcp/new_vm.sh --annotator jamie --image $IMAGE
EOF
