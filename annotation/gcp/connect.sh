#!/usr/bin/env bash
# Hand this to an annotator. It opens their annotation desktop.
#
#   ./connect.sh
#
# One-time setup on the annotator's own computer:
#   1. Install the Google Cloud CLI:  https://cloud.google.com/sdk/install
#   2. Install a VNC viewer (TigerVNC, RealVNC, or macOS's built-in Screen
#      Sharing).
#   3. Run: gcloud auth login      (use your CWRU account)
#
# After that, this script is the whole routine. Nothing on your own computer
# ever holds the recordings or your ELAN file -- they stay on the machine at
# the other end of the tunnel.
set -euo pipefail

ANNOTATOR="${ANNOTATOR:-${USER}}"
PROJECT="${PROJECT:?ask the project lead for the PROJECT value}"
ZONE="${ZONE:-us-east5-a}"
VM="annotate-${ANNOTATOR}"
PORT="${PORT:-5901}"

echo "Starting your annotation desktop..."
gcloud compute start-iap-tunnel "$VM" 5901 \
  --local-host-port="localhost:${PORT}" \
  --zone "$ZONE" --project "$PROJECT" &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null || true' EXIT

sleep 5
echo
echo "Ready. Connect your VNC viewer to:  localhost:${PORT}"
echo "On a Mac you can just run:          open vnc://localhost:${PORT}"
echo
echo "Leave this window open while you work. Close it when you're done"
echo "for the day -- your session and your file stay where they are."
wait $TUNNEL
