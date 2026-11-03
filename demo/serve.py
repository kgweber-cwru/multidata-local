#!/usr/bin/env python
"""Serve the encounter explorer locally.

    python demo/serve.py              # then open http://localhost:8765
    python demo/serve.py --port 9000 --host 0.0.0.0   # reachable from other machines

Standard library only, any env. Serves demo/app/ at / and data/demo/ at
/data/. It exists because Python's built-in http.server ignores Range
requests, and browsers can't seek in a video without them.

The bundles hold identifiable video and speech: keep the default host
(this machine only) unless everyone who can reach it is on the study's IRB.
"""
import argparse
import mimetypes
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "demo" / "app"
DATA = ROOT / "data" / "demo"
mimetypes.add_type("application/octet-stream", ".bin")


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        base, rel = (DATA, path[len("/data/"):]) if path.startswith("/data/") else (APP, path.lstrip("/"))
        full = (base / rel).resolve()
        if base.resolve() not in full.parents and full != base.resolve():
            return str(APP / "__nope__")
        return str(full)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        size = os.path.getsize(path)
        if not m:
            self.send_error(416)
            return None
        a, b = m.groups()
        if a == "":
            start, end = max(size - int(b), 0), size - 1
        else:
            start, end = int(a), min(int(b) if b else size - 1, size - 1)
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        n = getattr(self, "_remaining", None)
        if n is None:
            return super().copyfile(source, outputfile)
        try:
            while n > 0:
                buf = source.read(min(256 * 1024, n))
                if not buf:
                    break
                outputfile.write(buf)
                n -= len(buf)
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # the browser moved on: normal while seeking
        finally:
            self._remaining = None

    verbose = False

    def log_message(self, fmt, *args):
        if self.verbose:
            super().log_message(fmt + "  range=%s", *args, self.headers.get("Range"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args()
    Handler.verbose = args.verbose
    print(f"encounter explorer: http://localhost:{args.port}   (Ctrl-C to stop)")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
