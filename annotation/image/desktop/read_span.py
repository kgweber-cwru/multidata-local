#!/usr/bin/env python3
"""Print "START END" for a kit's excerpt window, or nothing. For submit-pass."""
import json
import pathlib
import sys

kit = pathlib.Path(sys.argv[1])
if kit.exists():
    data = json.loads(kit.read_text())
    if data.get("span_start") is not None and data.get("span_end") is not None:
        print(data["span_start"], data["span_end"])
