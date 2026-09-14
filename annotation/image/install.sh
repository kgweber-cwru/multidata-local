#!/usr/bin/env bash
# Build the annotator desktop. Runs as root on a plain Debian 12 VM during
# image build (annotation/gcp/build_image.sh), never on a running annotator's
# machine.
#
# What you get: Xfce, a VNC server on :1, ELAN, three desktop launchers, and
# the guide. Nothing else.
set -euo pipefail

# ---------------------------------------------------------------------------
# ELAN. Set these before building -- take both from the ELAN download page.
# The version is pinned on purpose: an unpinned upgrade partway through a
# corpus is a change-control event nobody notices (standards §12), and every
# kit records which version it was made with.
# ---------------------------------------------------------------------------
ELAN_VERSION="${ELAN_VERSION:-7.1}"
ELAN_DEB_URL="${ELAN_DEB_URL:?set ELAN_DEB_URL to the ELAN ${ELAN_VERSION} .deb download URL}"
ELAN_DEB_SHA256="${ELAN_DEB_SHA256:?set ELAN_DEB_SHA256 to the published checksum}"

ANNOTATOR_HOME=/etc/skel
IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  xfce4 xfce4-terminal dbus-x11 \
  tigervnc-standalone-server tigervnc-common \
  vlc libavcodec-extra \
  fonts-dejavu fonts-liberation \
  firefox-esr pandoc zenity \
  python3 curl ca-certificates cron

# The launchers use zenity for their handful of dialogs. gcloud moves kits and
# submissions; GCE Debian images normally ship it, but don't assume.
if ! command -v gcloud >/dev/null; then
  echo "==> google-cloud-cli"
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
http://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update -qq
  apt-get install -y -qq google-cloud-cli
fi

# A browser is here for one reason: to read the guide (§ below). That is also
# why it is acceptable -- annotators are trusted, and the earlier draft's
# lockdown was cut on review (annotator_image_design.md §7.4). If that decision
# is ever reversed, this line and the guide viewer both change together.

echo "==> the annotator account"
# One account, named the same on every machine, so the launchers and the VNC
# service don't have to know who is using it. Which person it belongs to is
# decided by who is granted IAP/OS Login access to the machine, not by a
# username here.
id annotator >/dev/null 2>&1 || adduser --disabled-password --gecos "" annotator
adduser annotator video    # so VLC/ELAN can reach the display hardware paths

echo "==> ELAN $ELAN_VERSION"
curl -fsSL "$ELAN_DEB_URL" -o /tmp/elan.deb
echo "$ELAN_DEB_SHA256  /tmp/elan.deb" | sha256sum -c -
apt-get install -y -qq /tmp/elan.deb
rm /tmp/elan.deb
echo "$ELAN_VERSION" > /etc/elan-version

echo "==> ELAN preferences (autosave only -- see elan_prefs/NOTES.md)"
install -d -m 755 "$ANNOTATOR_HOME/.elan_data"
cp -r "$IMAGE_DIR/elan_prefs/." "$ANNOTATOR_HOME/.elan_data/"
rm -f "$ANNOTATOR_HOME/.elan_data/NOTES.md"
if [[ -z "$(ls -A "$ANNOTATOR_HOME/.elan_data")" ]]; then
  echo
  echo "  !! No ELAN preferences to install -- autosave is NOT set."
  echo "  !! The image will work, but the guide's 'save often' is back to being"
  echo "  !! the annotator's problem. See elan_prefs/NOTES.md for the one-time"
  echo "  !! step that fixes this."
  echo
fi

echo "==> where cases get staged"
install -d -m 755 /srv/multidata/case
# The same path every kit's .eaf points at. Changing it means changing
# CANONICAL_ROOT in annotation/make_kit.py, or every existing kit breaks.

echo "==> helper scripts"
install -m 755 "$IMAGE_DIR/desktop/stage-case"       /usr/local/bin/stage-case
install -m 755 "$IMAGE_DIR/desktop/start-annotating" /usr/local/bin/start-annotating
install -m 755 "$IMAGE_DIR/desktop/submit-pass"      /usr/local/bin/submit-pass
install -m 755 "$IMAGE_DIR/desktop/open-guide"       /usr/local/bin/open-guide
install -m 755 "$IMAGE_DIR/desktop/read_span.py"       /usr/local/bin/read_span.py
install -m 755 "$IMAGE_DIR/desktop/write_submission.py" /usr/local/bin/write_submission.py
install -m 755 "$IMAGE_DIR/../submit_checks.py"        /usr/local/bin/submit_checks.py

echo "==> desktop launchers"
install -d -m 755 "$ANNOTATOR_HOME/Desktop"
cp "$IMAGE_DIR/desktop/"*.desktop "$ANNOTATOR_HOME/Desktop/"
chmod +x "$ANNOTATOR_HOME/Desktop/"*.desktop

echo "==> the guide, as local HTML"
# build_image.sh copies the two markdown docs into image/docs before sending
# the tree over. Local copies, not links: there is no guarantee of network
# access, and a guide that needs it is unavailable exactly when someone is
# stuck.
install -d -m 755 /usr/local/share/annotation-guide
for doc in annotator_guide transcription_standards; do
  if [[ -f "$IMAGE_DIR/docs/$doc.md" ]]; then
    pandoc -s --metadata title="$doc" \
      "$IMAGE_DIR/docs/$doc.md" -o "/usr/local/share/annotation-guide/$doc.html"
  else
    echo "  !! $doc.md missing -- the guide launcher will not work"
  fi
done

echo "==> VNC on :1 (port 5901)"
cat > "$ANNOTATOR_HOME/.vnc-xstartup" <<'EOF'
#!/bin/sh
unset SESSION_MANAGER DBUS_SESSION_BUS_ADDRESS
exec startxfce4
EOF
chmod +x "$ANNOTATOR_HOME/.vnc-xstartup"
install -m 644 "$IMAGE_DIR/desktop/vncserver@.service" \
  /etc/systemd/system/vncserver@.service
systemctl enable vncserver@1.service

echo "==> five-minute backup of work in progress"
install -m 755 "$IMAGE_DIR/desktop/backup-work" /usr/local/bin/backup-work
cat > /etc/cron.d/annotation-backup <<'EOF'
# Copy work in progress up to the bucket. Insurance against losing days of
# irreplaceable human effort sitting on one disk -- not the hand-off, which is
# the Submit button. Object versioning on the bucket makes a copy that races a
# save recoverable.
*/5 * * * * root /usr/local/bin/backup-work >/dev/null 2>&1
EOF

echo
echo "Done. Record the ELAN version ($ELAN_VERSION) with the image."
echo "Before handing this to anyone, work through the acceptance checklist in"
echo "docs/annotator_image_design.md §6 -- especially playback, with a real kit."
