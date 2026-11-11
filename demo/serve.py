#!/usr/bin/env python
"""Serve the encounter explorer.

    python demo/serve.py              # then open http://localhost:8765
    python demo/serve.py --port 9000 --host 0.0.0.0   # reachable from other machines

Standard library only, any env. Serves the app (demo/app/) at / and the
built bundles (data/demo/) at /data/. It exists because Python's built-in
http.server ignores Range requests, and browsers can't seek in a video
without them.

Settings, flag first, then environment variable, then default:

  --host  HOST        127.0.0.1 (this machine only)
  --port  PORT        8765 (Cloud Run sets PORT)
  --data  DEMO_DATA   data/demo/ in this checkout
          DEMO_APP    the app/ folder beside this file

Open-ended range requests ("bytes=N-", which is how browsers ask for video)
are answered in pieces of at most MAX_CHUNK bytes; the browser asks for the
next piece as it plays. That keeps every response under Cloud Run's 32 MiB
HTTP/1 limit and keeps reads from a mounted bucket small. /healthz answers
"ok" for load-balancer and container health checks.

The bundles hold identifiable video and speech: anyone who can reach the
server can see them.
"""
import argparse
import mimetypes
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = Path(os.environ.get("DEMO_APP", HERE / "app"))
DATA = Path(os.environ.get("DEMO_DATA", HERE.parent / "data" / "demo"))
MAX_CHUNK = 8 * 1024 * 1024
MEDIA = (".mp4", ".bin")             # large and unchanging once built: let browsers cache them
mimetypes.add_type("application/octet-stream", ".bin")
mimetypes.add_type("text/javascript", ".js")


class Handler(SimpleHTTPRequestHandler):
    verbose = False

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        base, rel = (DATA, path[len("/data/"):]) if path.startswith("/data/") else (APP, path.lstrip("/"))
        full = (base / rel).resolve()
        if base.resolve() not in full.parents and full != base.resolve():
            return str(APP / "__nope__")
        return str(full)

    def do_GET(self):
        if self.path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        cache = "private, max-age=86400" if self.path.split("?")[0].endswith(MEDIA) else "no-cache"
        self.send_header("Cache-Control", cache)
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        if not rng:
            if size <= MAX_CHUNK:
                return super().send_head()
            rng = "bytes=0-"          # a big file without a Range: send the first piece as a 206
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        if not m or m.groups() == ("", ""):
            self.send_error(416)
            return None
        a, b = m.groups()
        if a == "":
            start, end = max(size - int(b), 0), size - 1
        else:
            start, end = int(a), min(int(b) if b else size - 1, size - 1)
        end = min(end, start + MAX_CHUNK - 1)
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

    def log_message(self, fmt, *args):
        if self.verbose:
            super().log_message(fmt + "  range=%s", *args, self.headers.get("Range"))


def main():
    global DATA
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--data", default=None, help="folder of built bundles (default: DEMO_DATA or data/demo)")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args()
    if args.data:
        DATA = Path(args.data)
    Handler.verbose = args.verbose
    print(f"encounter explorer on {args.host}:{args.port}, data from {DATA}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
