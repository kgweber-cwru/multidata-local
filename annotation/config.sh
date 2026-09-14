# Project settings. Sourced by every script in gcp/, and read by the Python
# scripts too, so none of these has to be typed or remembered.
#
# `: "${NAME:=value}"` means "use this unless it is already set", so anything
# in your environment still wins:
#
#   ZONE=us-central1-a annotation/gcp/new_vm.sh --annotator jamie
#
# Tracked in git on purpose. None of it is secret, and the real values are more
# useful here than placeholders would be. Credentials never belong in this file.

# --- where things live --------------------------------------------------------
: "${ANNOTATION_BUCKET:=gs://som-anno-data-bucket}"

# us-east5 is Columbus -- the closest region to Cleveland, which matters
# because segmentation boundaries land on a keypress (see the image design §5).
: "${REGION:=us-east5}"
: "${ZONE:=us-east5-a}"
: "${SUBNET:=default}"

# --- the machine image --------------------------------------------------------
: "${ANNOTATOR_IMAGE:=annotator-v1}"
: "${ELAN_VERSION:=7.1}"
: "${ELAN_DEB_URL:=https://www.mpi.nl/tools/elan/ELAN_7-1_linux.deb}"
: "${ELAN_DEB_SHA256:=01c52cb5cde3090b2e9a46a299936b7a358363e7231507f7fc8a01fba7394073}"

# --- who the annotator accounts belong to -------------------------------------
: "${ANNOTATOR_EMAIL_DOMAIN:=case.edu}"
