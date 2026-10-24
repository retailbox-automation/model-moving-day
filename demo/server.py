"""The demo's HTTP API (standard library only; one thread per request). REPLAY only: every answer comes from
demo/data/census.json, loaded once at start; no file is read and no model is called per request.

    python -m demo.server            PORT (default 8080) and HOST (default 127.0.0.1) from the environment

GET  /                    what this is and the endpoints (the page comes later)
GET  /api/census          the whole census: headline, model per call site, notes, runs, found-not-measured, patches
GET  /api/site/<site>     one call site, e.g. /api/site/instructor/v2/core/retry.py:224: its row and every cell of it
                          in every decision run
GET  /api/run/<run_id>    one decision run with all its cells
GET  /api/patch/<repo>    one migration branch: result, confirm runs, PR text and diff
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

DATA = Path(__file__).resolve().parent / "data" / "census.json"
ENDPOINTS = ["/api/census", "/api/site/<site>", "/api/run/<run_id>", "/api/patch/<repo>"]


class App:
    def __init__(self, payload=None):
        p = payload or json.loads(DATA.read_text())
        c = p["census"]
        self.about = dict(p["about"], endpoints=ENDPOINTS, sites=[s["site"] for s in c["sites"]],
                          runs=list(c["runs"]), repos=[x["target"] for x in c["patches"]])
        self.census = c
        self.sites = {s["site"]: self._site(c, s) for s in c["sites"]}
        self.runs = c["runs"]
        self.patches = {x["target"]: dict(x, **p["patch_files"].get(x["target"], {})) for x in c["patches"]}
        self.cache = {}  # path -> encoded body: the data never changes while the process runs

    @staticmethod
    def _site(c, row):
        cells = [dict(cell, run_id=rid) for rid, r in c["runs"].items() for cell in r["cells"] if cell["site"] == row["site"]]
        patch = next((x["target"] for x in c["patches"] if row["site"] in x["moved"]), None)
        return {"row": row, "cells": cells, "patch": patch,
                "note": c["site_notes"][row["note"] - 1] if row.get("note") else None}

    def handle(self, method, path):
        if method != "GET":
            return 405, {"error": "Only GET."}
        path = unquote(path.split("?", 1)[0])
        if path in ("/", "/api", "/api/"):
            return 200, self.about
        if path == "/api/census":
            return 200, self.census
        for prefix, table in (("/api/site/", self.sites), ("/api/run/", self.runs), ("/api/patch/", self.patches)):
            if path.startswith(prefix):
                key = path[len(prefix):]
                if key in table:  # exact keys only: nothing is looked up on disk
                    return 200, table[key]
                return 404, {"error": f"Unknown {prefix.split('/')[2]}. GET / lists the known ones."}
        return 404, {"error": "Not found. See GET / for the endpoints."}

    def response(self, method, path):
        key = (method, path)
        if key not in self.cache:
            status, payload = self.handle(method, path)
            body = json.dumps(payload, ensure_ascii=False).encode()
            if status != 200 or len(self.cache) > 1000:
                return status, body
            self.cache[key] = (status, body)
        return self.cache[key]


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ModelMovingDayDemo/1"

        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=300" if status == 200 else "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _dispatch(self):
            self._send(*app.response(self.command if self.command != "HEAD" else "GET", self.path))

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

        def log_message(self, fmt, *args):  # one line per request on stderr
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")
    return Handler


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the default backlog of 5 resets connections when several judges arrive at once
    daemon_threads = True


def serve(app=None, host=None, port=None):
    host = host or os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT") or 8080) if port is None else int(port)
    return Server((host, port), make_handler(app or App()))


if __name__ == "__main__":
    httpd = serve()
    print(f"Model Moving Day demo on http://{httpd.server_address[0]}:{httpd.server_address[1]}/")
    httpd.serve_forever()
