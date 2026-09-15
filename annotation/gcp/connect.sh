#!/usr/bin/env bash
# Hand this to an annotator. It opens their annotation desktop.
#
#   ./connect.sh
#
# One-time setup on the annotator's own computer:
#   1. Install the Google Cloud CLI:  https://cloud.google.com/sdk/install
#   2. Install a Remote Desktop client:
#        macOS    "Windows App" (formerly Microsoft Remote Desktop), free
#                 from the Mac App Store
#        Windows  already installed -- Remote Desktop Connection
#        Linux    Remmina, or any FreeRDP client
#   3. Run:  gcloud auth login              (use your CWRU account)
#            gcloud config set project <the name the project lead gives you>
#
# The project lead also gives you a username and password for the desktop.
# They are not your CWRU credentials; they belong to the machine.
#
# After that this script is the whole routine. Nothing on your own computer
# ever holds the recordings or your ELAN file -- they stay on the machine at
# the other end.
#
# ---------------------------------------------------------------------------
# How this connects, because the obvious way does not work.
#
# The desktop is served over RDP, not VNC, because VNC's protocol carries no
# audio at all and the whole job is listening.
#
# We reach it by tunnelling IAP to port 22 -- the path every other script here
# uses -- and letting SSH forward a local port to the machine's RDP port.
# Pointing IAP straight at the desktop port does not work: IAP connects to the
# VM on its internal interface, and the desktop is not listening there. Going
# via SSH is also the better arrangement:
#
#   * The firewall only ever needs port 22 open to IAP's range.
#   * The real authentication is Google IAM, against a named person; the VNC
#     password is a second lock inside an already-authenticated tunnel.
# ---------------------------------------------------------------------------
set -euo pipefail

# Defaults, each overridable by a flag or an environment variable. The
# annotator name defaults to your local username, which is only a guess -- the
# machine is named for whatever the project lead passed to new_vm.sh, and that
# is often a CWRU id rather than whatever your own laptop calls you. Pass
# --annotator when they differ.
ANNOTATOR="${ANNOTATOR:-${USER}}"
ZONE="${ZONE:-us-east5-a}"
PROJECT="${PROJECT:-}"
PORT="${PORT:-3389}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --annotator) ANNOTATOR="$2"; shift 2 ;;
    --project)   PROJECT="$2";   shift 2 ;;
    --zone)      ZONE="$2";      shift 2 ;;
    --port)      PORT="$2";      shift 2 ;;
    -h|--help)
      cat <<'USAGE'
Open your annotation desktop.

  ./connect.sh [--annotator NAME] [--project ID] [--zone ZONE] [--port N]

--annotator  the name your machine is registered under (ask the project lead);
             defaults to your local username, which is often not the same
--project    defaults to your current gcloud project
USAGE
      exit 0 ;;
    *) echo "unknown argument: $1  (try --help)" >&2; exit 1 ;;
  esac
done

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
VM="annotate-${ANNOTATOR}"

if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  cat >&2 <<'EOF'
No Google Cloud project is set. Run this once, with the project name the
project lead gave you:

  gcloud config set project <project-name>

Then run this script again.
EOF
  exit 1
fi

# Confirm the machine exists before opening a tunnel to it. Without this, a
# wrong --annotator produces "failed to connect to backend", which reads like a
# broken desktop rather than a name that doesn't exist.
if ! gcloud compute instances describe "$VM" \
     --zone "$ZONE" --project "$PROJECT" >/dev/null 2>&1; then
  echo "No machine called $VM in $PROJECT ($ZONE)." >&2
  echo >&2
  # Best-effort: an annotator may not have permission to list instances.
  OTHERS="$(gcloud compute instances list --project "$PROJECT" \
            --filter='name~^annotate-' --format='value(name)' 2>/dev/null || true)"
  if [[ -n "$OTHERS" ]]; then
    echo "Machines that do exist:" >&2
    echo "$OTHERS" | sed 's/^annotate-/  --annotator /' >&2
  else
    echo "Check the name with the project lead, then:" >&2
    echo "  ./connect.sh --annotator THEIR_NAME_FOR_YOU" >&2
  fi
  exit 1
fi

STATUS="$(gcloud compute instances describe "$VM" --zone "$ZONE" \
          --project "$PROJECT" --format='value(status)' 2>/dev/null)"
if [[ "$STATUS" != "RUNNING" ]]; then
  echo "$VM is $STATUS, not running. Ask the project lead to start it." >&2
  exit 1
fi

# Ask the machine whether the desktop is actually listening, BEFORE opening the
# tunnel.
#
# This costs one SSH round trip and is worth it, because the obvious check is
# worthless: SSH binds the local end of a -L forward immediately, whether or not
# the remote side can be connected to. So a local "is the port open" test always
# passes, the script says "Ready", and the real failure surfaces much later as
# "channel N: open failed: connect failed: Connection refused" in the middle of
# the session -- which reads like a network problem and isn't one.
echo "Checking the desktop is running..."
if ! gcloud compute ssh "$VM" --zone "$ZONE" --project "$PROJECT" \
     --tunnel-through-iap --command "ss -lnt | grep -q ':3389'" >/dev/null 2>&1; then
  cat >&2 <<EOF

Reached $VM, but nothing is serving a desktop on it.

That is a problem on the machine, not on your computer. Send the project lead
this message; for them, the place to start is:

  gcloud compute ssh $VM --zone $ZONE --project $PROJECT --tunnel-through-iap \
    --command 'systemctl status xrdp --no-pager -l; sudo ss -lntp | grep 3389'

EOF
  exit 1
fi

# Check the local port is free first. A leftover tunnel from an earlier attempt
# would make the readiness check below succeed against the wrong thing, and then
# the viewer would connect to nothing useful.
if nc -z localhost "$PORT" 2>/dev/null; then
  cat >&2 <<EOF
Something is already using port $PORT on this computer -- most likely a
connect.sh from earlier that is still running.

Close that window, or use a different port for this one:

  PORT=5902 ./connect.sh

EOF
  exit 1
fi

echo "Starting your annotation desktop..."

gcloud compute ssh "$VM" \
  --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap \
  -- -N -L "${PORT}:localhost:3389" &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null || true' EXIT

# Wait for the forward to be usable rather than guessing at a sleep. The SSH
# connection and IAP both take a few seconds, and saying "Ready" over the top
# of a failure is how this used to waste people's time.
for _ in $(seq 1 20); do
  if ! kill -0 "$TUNNEL" 2>/dev/null; then
    cat >&2 <<EOF

The connection could not be opened. The message above says why; the usual
causes are:

  * Your machine ($VM) is stopped. Ask the project lead to start it.
  * You do not have access to it yet. Ask the project lead.

EOF
    exit 1
  fi
  # Only proves SSH has bound the local end -- which it does eagerly. Whether
  # the far end answers was settled by the pre-flight check above.
  if nc -z localhost "$PORT" 2>/dev/null; then
    READY=yes
    break
  fi
  sleep 1
done

if [[ "${READY:-no}" != "yes" ]]; then
  cat >&2 <<EOF

The tunnel did not finish opening. Nothing is wrong with the machine -- the
desktop was answering a moment ago -- so this is worth simply trying again.

EOF
  exit 1
fi

cat <<EOF

Ready. Open your Remote Desktop client and connect to:

  localhost:${PORT}

Sign in with the desktop username and password the project lead gave you --
not your CWRU credentials.

FIRST TIME ONLY, and this is the point of the whole thing: in the client's
settings for this connection, turn audio ON ("Play sound: On this computer"
in the Mac app). Without it you get a silent desktop, and you cannot
transcribe what you cannot hear.

Leave this window open while you work. Close it when you're done for the day --
your session and your file stay where they are.
EOF

wait $TUNNEL
