#!/usr/bin/env bash
# Build the annotator desktop. Runs as root on a plain Debian 12 VM during
# image build (annotation/gcp/build_image.sh), never on a running annotator's
# machine.
#
# What you get: Xfce served over RDP (port 3389) with audio, ELAN, three
# desktop launchers, and the guide. Nothing else.
set -euo pipefail

# ---------------------------------------------------------------------------
# ELAN. These arrive from build_image.sh, which passes them through both the
# SSH hop and sudo -- neither of which carries an environment on its own. If
# you are running this script by hand, set them on the command line:
#
#   sudo ELAN_DEB_URL=... ELAN_DEB_SHA256=... bash install.sh
#
# The version is pinned on purpose: an unpinned upgrade partway through a
# corpus is a change-control event nobody notices (standards §12), and every
# kit records which version it was made with.
# ---------------------------------------------------------------------------
ELAN_VERSION="${ELAN_VERSION:-7.1}"
ELAN_DEB_URL="${ELAN_DEB_URL:?set ELAN_DEB_URL to the ELAN ${ELAN_VERSION} .deb download URL (see the comment above -- if you set it in your own shell, it does not reach this script)}"
# Optional. Verified when given; printed when not, so you can pin it next time.
ELAN_DEB_SHA256="${ELAN_DEB_SHA256:-}"

ANNOTATOR_HOME=/etc/skel
IMAGE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# The desktop and the media stack get their Recommends.
#
# An earlier version installed everything with --no-install-recommends, which
# was a mistake worth spelling out: `xfce4` is a metapackage whose Recommends
# *are* most of the desktop. Stripping them produced a machine that installed
# cleanly and had no working session. For media it is worse than a nuisance --
# a silently missing codec is exactly the failure this image most needs to
# avoid, and disk is not scarce here.
#
# xauth, x11-xkb-utils and xfonts-base are named explicitly as well as being
# pulled in, so that a future Recommends change cannot quietly drop them.
apt-get install -y -qq \
  xfce4 xfce4-terminal dbus-x11 \
  xauth x11-xkb-utils xfonts-base \
  vlc libavcodec-extra

# Leaf tools, where skipping recommends saves real space and risks nothing.
apt-get install -y -qq --no-install-recommends \
  fonts-dejavu fonts-liberation \
  firefox-esr pandoc zenity \
  python3 curl ca-certificates cron

# ---------------------------------------------------------------------------
# RDP, with audio.
#
# The desktop is served over RDP rather than VNC for one reason: VNC's protocol
# has no audio channel, and the entire job is listening. See
# docs/annotator_image_design.md §4b.
#
# Audio needs PipeWire, not PulseAudio: pulseaudio-module-xrdp does not exist
# as a Debian 12 package at all, while pipewire-module-xrdp does -- in
# backports, which sit at apt priority 100 and so need an explicit -t.
#
# Versions are PINNED. A backport moves under you, and "rebuild the image" has
# to keep meaning "rebuild the same image" (standards §12, same reasoning as
# ELAN). Bump these deliberately, and re-run the fidelity test in §4b when you
# do -- this is the audio path for phonetic judgements, not background music.
# ---------------------------------------------------------------------------
XRDP_VERSION="${XRDP_VERSION:-0.9.24-5~bpo12+1}"
PIPEWIRE_VERSION="${PIPEWIRE_VERSION:-1.4.2-1~bpo12+1}"
WIREPLUMBER_VERSION="${WIREPLUMBER_VERSION:-0.5.8-1~bpo12+1}"
PW_XRDP_VERSION="${PW_XRDP_VERSION:-0.2-2~bpo12+1}"

if ! grep -rq bookworm-backports /etc/apt/sources.list /etc/apt/sources.list.d/ \
     /etc/apt/mirrors/ 2>/dev/null; then
  echo "    adding bookworm-backports"
  echo "deb http://deb.debian.org/debian bookworm-backports main" \
    > /etc/apt/sources.list.d/backports.list
  apt-get update -qq
fi

# xfce4's Recommends bring PulseAudio in, and pipewire-pulse replaces it. Say
# so, rather than leaving a reader to wonder why apt removed something.
if dpkg -l pulseaudio 2>/dev/null | grep -q '^ii'; then
  echo "    replacing pulseaudio with pipewire-pulse"
fi

echo "==> RDP and audio (xrdp $XRDP_VERSION, pipewire $PIPEWIRE_VERSION)"
apt-get install -y -qq -t bookworm-backports \
  "xrdp=$XRDP_VERSION" \
  xorgxrdp \
  "pipewire=$PIPEWIRE_VERSION" \
  "pipewire-pulse=$PIPEWIRE_VERSION" \
  "pipewire-bin=$PIPEWIRE_VERSION" \
  "wireplumber=$WIREPLUMBER_VERSION" \
  "pipewire-module-xrdp=$PW_XRDP_VERSION"

# xrdp needs to read the snakeoil TLS certificate.
adduser xrdp ssl-cert >/dev/null

# Show the channel settings rather than editing keys blind. Audio needs the
# rdpsnd channel; xrdp enables it by default when the module is present, and
# guessing at .ini keys has cost this project enough already.
echo "    xrdp channel settings, as shipped:"
grep -inE 'allow_channels|rdpsnd|^port|^address' /etc/xrdp/xrdp.ini \
  | sed 's/^/      /' || true

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

echo "==> checking the packages gave us what we need"
# Early, on purpose. Every one of these is assumed later in this script, and a
# wrong package name should cost seconds here rather than surfacing at the end
# of the build -- or worse, when an annotator clicks something.
MISSING=""
for cmd in xrdp startxfce4 xauth xrdb pandoc zenity \
          firefox-esr python3 gcloud pipewire; do
  command -v "$cmd" >/dev/null || MISSING="$MISSING $cmd"
done
if [[ -n "$MISSING" ]]; then
  echo "  !! not on PATH after installing packages:$MISSING" >&2
  echo "  !! the package names in this script need fixing for this Debian" >&2
  exit 1
fi

echo "==> ELAN $ELAN_VERSION"
echo "    from $ELAN_DEB_URL"
# -f so an HTTP error is a failure rather than a saved error page, -L to follow
# the redirect the MPI download links use.
curl -fsSL "$ELAN_DEB_URL" -o /tmp/elan.deb

GOT_SHA256="$(sha256sum /tmp/elan.deb | cut -d' ' -f1)"
if [[ -n "$ELAN_DEB_SHA256" ]]; then
  if [[ "$GOT_SHA256" != "$ELAN_DEB_SHA256" ]]; then
    echo "  !! checksum mismatch" >&2
    echo "  !!   expected $ELAN_DEB_SHA256" >&2
    echo "  !!   got      $GOT_SHA256" >&2
    exit 1
  fi
  echo "    checksum ok"
else
  echo "    checksum not pinned. Downloaded:"
  echo "      export ELAN_DEB_SHA256=$GOT_SHA256"
  echo "    Set that before the next rebuild so it installs the same ELAN."
fi

# apt refuses a file that isn't a real .deb, which catches a URL that quietly
# served something else.
ELAN_PKG="$(dpkg-deb -f /tmp/elan.deb Package)"
apt-get install -y -qq /tmp/elan.deb
rm /tmp/elan.deb
echo "$ELAN_VERSION" > /etc/elan-version

# The ELAN .deb does not put anything called `elan` on PATH -- it installs its
# own tree with a launcher inside it, and the launcher is capitalised. Rather
# than guess at the install path (which would change between ELAN releases and
# break silently), ask dpkg what the package actually laid down and link the
# launcher to a stable name. `start-annotating` calls plain `elan`.
echo "    finding the launcher in package $ELAN_PKG"
# Match by pattern, not by a list of names. ELAN 7.1's launcher is
# /opt/elan-7.1/bin/ELAN_7.1 -- the version is in the basename, so any list of
# exact names is wrong again at 7.2. Rule: an executable whose name starts with
# "elan" (any case), skipping the bundled JRE under lib/ (java, keytool, jexec,
# jspawnhelper).
ELAN_BIN=""
while read -r f; do
  [[ -f "$f" && -x "$f" ]] || continue
  case "$f" in */lib/*) continue ;; esac
  case "$(basename "$f" | tr '[:upper:]' '[:lower:]')" in
    elan*) ELAN_BIN="$f"; break ;;
  esac
done < <(dpkg -L "$ELAN_PKG")

if [[ -z "$ELAN_BIN" ]]; then
  echo "  !! Could not find ELAN's launcher in package $ELAN_PKG." >&2
  echo "  !! Executables the package installed:" >&2
  dpkg -L "$ELAN_PKG" \
    | while read -r f; do [[ -f "$f" && -x "$f" ]] && echo "  !!   $f" >&2; done
  echo "  !! Pick the launcher from that list and widen the pattern in the" >&2
  echo "  !! loop just above this message in install.sh." >&2
  exit 1
fi

echo "    launcher: $ELAN_BIN"
# A wrapper rather than a symlink, deliberately. Java launcher scripts commonly
# locate their jars with `dirname $0`, and through a symlink $0 is the symlink's
# own path -- so the launcher would look for ELAN's files in /usr/local/bin and
# not find them. `exec` with the absolute path makes $0 the real launcher.
cat > /usr/local/bin/elan <<WRAPPER
#!/bin/sh
exec "$ELAN_BIN" "\$@"
WRAPPER
chmod 755 /usr/local/bin/elan
echo "$ELAN_BIN" > /etc/elan-launcher

echo "==> ELAN preferences (autosave only -- see elan_prefs/NOTES.md)"
# elan_prefs/ holds the CONTENTS of ~/.elan_data. A directory or file of that
# name nested inside would land at ~/.elan_data/.elan_data and be ignored by
# ELAN -- which looks done without being done, so stop instead.
if [[ -e "$IMAGE_DIR/elan_prefs/.elan_data" ]]; then
  echo "  !! elan_prefs/.elan_data exists. This directory holds the *contents*" >&2
  echo "  !! of ~/.elan_data, not a copy of the directory itself -- nested like" >&2
  echo "  !! this, ELAN would never read it. See elan_prefs/NOTES.md." >&2
  exit 1
fi
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

echo "==> the desktop password"
# xrdp authenticates through PAM, so the annotator account needs a real
# password. This replaces the VNC password, which only ever existed because
# macOS Screen Sharing refuses a server offering no authentication -- still one
# credential, just a different kind.
#
# Not in annotation/config.sh, because that file is tracked in git. Supply
# DESKTOP_PASSWORD to reproduce a previous image; otherwise one is generated
# and printed at the end of the build.
DESKTOP_PASSWORD="${DESKTOP_PASSWORD:-${VNC_PASSWORD:-$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 12)}}"

echo "==> the desktop session"
# xrdp runs ~/.xsession. Same Xfce desktop the VNC setup served, reached a
# different way.
cat > "$ANNOTATOR_HOME/.xsession" <<'EOF'
#!/bin/sh
exec startxfce4
EOF
chmod +x "$ANNOTATOR_HOME/.xsession"

echo "==> the annotator account"
# CREATED LAST, ON PURPOSE. adduser copies /etc/skel as it stands at that
# moment, so the account has to come after everything above has been written
# into it -- the ELAN preferences, the desktop launchers, and ~/.xsession.
# Creating it earlier produces a machine that builds cleanly and boots to
# nothing, because the home directory is empty and the VNC session has no
# xstartup to run.
#
# One account, named the same on every machine, so the launchers and the
# session service don't have to know who is using it. Which person it belongs
# to is decided by who is granted IAP access to the machine, not by a username.
if id annotator >/dev/null 2>&1; then
  echo "  !! the annotator account already exists, so /etc/skel was NOT copied" >&2
  echo "  !! into it. This script is meant to run once on a fresh VM." >&2
  exit 1
fi
adduser --disabled-password --gecos "" annotator
adduser annotator video    # so VLC/ELAN can reach the display hardware paths
adduser annotator audio    # and the audio devices PipeWire exposes
printf '%s\n%s\n' "$DESKTOP_PASSWORD" "$DESKTOP_PASSWORD" | passwd annotator >/dev/null 2>&1

echo "==> RDP on 3389"
# xrdp ships its own systemd units; enable rather than write our own. The
# previous VNC unit is gone -- two desktop servers competing for display :1 is
# exactly the "X server already running" mess this project already hit once.
systemctl enable xrdp.service
systemctl enable xrdp-sesman.service

echo "==> proving the desktop actually starts"
# `systemctl is-enabled` is not enough: enabled means it will be *attempted* at
# boot, not that it works. Three separate "cannot connect" rounds came from
# trusting that, so the build starts the service and requires it to answer.
#
# What this canNOT check is whether audio reaches a client -- that needs an RDP
# client and a pair of ears (image design §4b). It rules out the obvious
# failures; the fidelity test is still a person's job.
systemctl restart xrdp.service || true

for _ in $(seq 1 30); do
  ss -lnt 2>/dev/null | grep -q ':3389' && break
  sleep 1
done

desktop_failed() {
  echo "  !! $1" >&2
  systemctl status xrdp --no-pager -l >&2 || true
  journalctl -u xrdp -u xrdp-sesman --no-pager -n 40 >&2 || true
  exit 1
}

ss -lnt 2>/dev/null | grep -q ':3389' \
  || desktop_failed "nothing ever listened on 3389."

sleep 5
systemctl is-active --quiet xrdp.service \
  || desktop_failed "3389 opened but xrdp did not stay up."

echo "    listening on 3389, and still up after 5s"
ss -lnt | grep ':3389' | sed 's/^/      /'

echo "==> five-minute backup of work in progress"
install -m 755 "$IMAGE_DIR/desktop/backup-work" /usr/local/bin/backup-work
cat > /etc/cron.d/annotation-backup <<'EOF'
# Copy work in progress up to the bucket. Insurance against losing days of
# irreplaceable human effort sitting on one disk -- not the hand-off, which is
# the Submit button. Object versioning on the bucket makes a copy that races a
# save recoverable.
*/5 * * * * root /usr/local/bin/backup-work >/dev/null 2>&1
EOF

# ---------------------------------------------------------------------------
# Self-check. Every one of these has been, or could be, a build that finishes
# cleanly and produces a machine that does nothing -- which is the worst
# outcome, because it is only discovered by a person trying to work. Cheap to
# check here, expensive to find later.
# ---------------------------------------------------------------------------
echo "==> checking the build"
FAILED=0
check() {
  if eval "$2"; then
    echo "    ok    $1"
  else
    echo "    FAIL  $1" >&2
    FAILED=1
  fi
}

check "elan runs from PATH"          "command -v elan >/dev/null"
check "the launcher it points at exists" \
      "[[ -x \"\$(cat /etc/elan-launcher)\" ]]"
check "the annotator account exists"  "id annotator >/dev/null 2>&1"
# These four prove /etc/skel was populated BEFORE the account was created.
# Getting that order wrong is invisible until someone tries to connect.
check "~/.xsession is there"          "[[ -x /home/annotator/.xsession ]]"
check "the account has a password"    "passwd -S annotator | grep -q ' P '"
check "the three launchers are there" \
      "[[ \$(ls /home/annotator/Desktop/*.desktop 2>/dev/null | wc -l) -eq 3 ]]"
check "~/.elan_data exists"           "[[ -d /home/annotator/.elan_data ]]"
check "the desktop service is enabled" \
      "systemctl is-enabled xrdp.service >/dev/null 2>&1"
check "the xrdp audio module is installed" \
      "ls /usr/lib/pipewire-0.3/libpipewire-module-xrdp* >/dev/null 2>&1"
check "pipewire-pulse is present"     "command -v pipewire-pulse >/dev/null"
check "the staging directory exists"  "[[ -d /srv/multidata/case ]]"
check "the guide rendered"            \
      "[[ -f /usr/local/share/annotation-guide/annotator_guide.html ]]"
check "stage-case is installed"       "[[ -x /usr/local/bin/stage-case ]]"
check "the submit checks run"         \
      "python3 /usr/local/bin/submit_checks.py --help >/dev/null 2>&1"

if [[ $FAILED -ne 0 ]]; then
  echo >&2
  echo "The build finished but the machine is not usable. Do not snapshot it." >&2
  exit 1
fi

echo
echo "================================================================"
echo "  Desktop login for this image:  annotator / $DESKTOP_PASSWORD"
echo
echo "  Annotators need both to open the desktop over RDP. Keep the"
echo "  password somewhere that is not this repository. To rebuild an"
echo "  identical image, pass it back in as DESKTOP_PASSWORD."
echo "================================================================"
echo
echo "Done. Record the ELAN version ($ELAN_VERSION) with the image."
echo "Before handing this to anyone, work through the acceptance checklist in"
echo "docs/annotator_image_design.md §6 -- especially playback, with a real kit."
