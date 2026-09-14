#!/usr/bin/env python3
"""Write submission.json next to a finished pass. Called by submit-pass."""
import datetime
import json
import pathlib
import sys

case_dir, annotator, case_id = sys.argv[1:4]
elan_version = pathlib.Path("/etc/elan-version")

pathlib.Path(case_dir, "submission.json").write_text(json.dumps({
    "case_id": case_id,
    "annotator": annotator,
    "submitted_at": datetime.datetime.now().astimezone().isoformat(),
    "elan_version": elan_version.read_text().strip() if elan_version.exists() else "",
}, indent=2) + "\n")
