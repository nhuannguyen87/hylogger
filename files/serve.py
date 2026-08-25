#!/usr/bin/env python
"""Step 5 - serve data/site over http so the viewer can fetch its hole files.

Browsers block fetch() on file:// URLs, so opening index.html by double-clicking
shows a "needs a local server" message instead of the map. This serves the built
site on localhost with caching turned off, so a rebuild shows up on reload.

    python serve.py --out data
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "404" in (fmt % args):
            sys.stderr.write(f"  404 {self.path}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the built viewer on localhost.")
    parser.add_argument("--out", default=str(HERE / "data"), help="data root (default: ./data)")
    parser.add_argument("--site-root", help="override <out>/site")
    parser.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    args = parser.parse_args()

    data_root = Path(args.out).resolve()
    site_root = Path(args.site_root).resolve() if args.site_root else data_root / "site"
    if not (site_root / "index.html").is_file():
        parser.error(f"no index.html at {site_root}\nRun site.py first.")

    handler = functools.partial(Handler, directory=str(site_root))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        server = socketserver.TCPServer(("127.0.0.1", args.port), handler)
    except OSError as exc:
        parser.error(f"could not bind port {args.port}: {exc}\nTry --port 8001.")

    url = f"http://127.0.0.1:{args.port}/"
    print(f"serving {site_root}")
    print(f"  {url}   (ctrl-c to stop)")
    if not args.no_open:
        threading.Timer(0.7, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
