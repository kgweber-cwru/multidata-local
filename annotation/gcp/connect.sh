#!/usr/bin/env bash
# Hand this to an annotator. It opens their annotation desktop.
#
#   ./connect.sh
#
# One-time setup on the annotator's own computer:
#   1. Install the Google Cloud CLI:  https://cloud.google.com/sdk/install
#   2. Run:  gcloud auth login              (use your CWRU account)
#            gcloud config set project <the name the project lead gives you>
#
# On a Mac that is all -- Screen Sharing is already installed. On Windows or
# Linux you also need a VNC viewer (TigerVNC or RealVNC).
#
# The project lead gives you a VNC password as well. It is not your CWRU
# password; it belongs to the machine.
#
# After that this script is the whole routine. Nothing on your own computer
# ever holds the recordings or your ELAN file -- they stay on the machine at
# the other end.
#
# ---------------------------------------------------------------------------
# How this connects, because the obvious way does not work.
#
# The desktop listens on 127.0.0.1:5901 on the remote machine. Tunnelling IAP
# straight at port 5901 cannot work -- IAP connects to the VM on its internal
# interface, and nothing is listening there.
#
# So we go the way that does work: IAP to port 22, which is how every other
# script here reaches these machines, and then SSH forwards a local port to the
# remote loopback. That is also the better arrangement:
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
PORT="${PORT:-5901}"

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
  -- -N -L "${PORT}:localhost:5901" &
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
  if nc -z localhost "$PORT" 2>/dev/null; then
    READY=yes
    break
  fi
  sleep 1
done

if [[ "${READY:-no}" != "yes" ]]; then
  cat >&2 <<EOF

Connected to the machine, but the desktop on it is not answering.

That is a problem on the machine, not on your computer, so there is nothing
for you to fix -- send the project lead this message.

EOF
  exit 1
fi

cat <<EOF

Ready. Connect your VNC viewer to:  localhost:${PORT}
On a Mac you can just run:          open vnc://localhost:${PORT}

It will ask for a password. That is the VNC password the project lead gave
you -- not your CWRU password.

Leave this window open while you work. Close it when you're done for the day --
your session and your file stay where they are.
EOF

wait $TUNNEL
