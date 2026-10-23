"""OpenAI-compatible recording proxy that sits between a target repo's tests and Token Factory.

The target's SDK is pointed here (OPENAI_BASE_URL). For each request the proxy reads the call site and test from
the tracer headers, picks a route (per call site, else the default), normalizes the request for that Nemotron
rung, sends it through tf_client (spend reserved in a ledger first) and logs one JSON line per call. Routes:
  forward  - serve with the route's model (and reasoning switch / max_tokens floor)
  broken   - answer 200 with empty content and no tool calls, without calling any model (must-differ control)
Streaming requests are served from a non-streaming upstream call and re-emitted as server-sent events.

    python -m model_moving_day.proxy --port 8765 --routes routes.json --log calls.jsonl \
        --ledger ledger.jsonl --cap 0.50 --tag c1
"""
import argparse, json, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_moving_day import find
from model_moving_day import normalize as nz
from model_moving_day import tf_client as tf

TRACER_DIR = Path(__file__).resolve().parent / "tracer"
CHAT_PATHS = {"/v1/chat/completions", "/chat/completions"}


def _route(routes, site):
    r = dict(routes.get("default") or {})
    r.update((routes.get("sites") or {}).get(site, {}))
    return r


def _broken(body):
    return {"id": "mmd-broken-" + uuid.uuid4().hex[:8], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model"), "choices": [{"index": 0, "finish_reason": "stop",
                                                     "message": {"role": "assistant", "content": ""}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _sse(raw, include_usage):
    msg = raw["choices"][0].get("message") or {}
    base = {"id": raw.get("id", "mmd"), "object": "chat.completion.chunk", "created": raw.get("created", int(time.time())),
            "model": raw.get("model")}
    delta = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        delta["tool_calls"] = [dict(tc, index=i) for i, tc in enumerate(msg["tool_calls"])]
    chunks = [dict(base, choices=[{"index": 0, "delta": delta, "finish_reason": None}]),
              dict(base, choices=[{"index": 0, "delta": {}, "finish_reason": raw["choices"][0].get("finish_reason")}])]
    if include_usage:
        chunks.append(dict(base, choices=[], usage=raw.get("usage")))
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet; every call is in the JSONL log
        pass

    def _send(self, status, payload, content_type="application/json"):
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.log({"path": self.path, "method": "GET", "status": 501})
        self._send(501, {"error": {"message": f"model-moving-day proxy does not serve GET {self.path}"}})

    def do_POST(self):
        srv, t0 = self.server, time.time()
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        chain = [c for c in (self.headers.get("x-mmd-chain") or "").split("|") if c]
        site = find.resolve_site(chain, srv.routes.get("spans") or {}) if chain else (self.headers.get("x-mmd-site") or "unknown")
        test = (self.headers.get("x-mmd-test") or "").replace(" (call)", "").replace(" (setup)", "").replace(" (teardown)", "")
        rec = {"ts": round(t0, 3), "path": self.path, "site": site, "chain": chain, "test": test, "requested_model": body.get("model"),
               "stream": bool(body.get("stream"))}
        path = self.path.split("?")[0]
        if path not in CHAT_PATHS:
            rec.update(status=501)
            srv.log(rec)
            return self._send(501, {"error": {"message": f"model-moving-day proxy does not route {path}"}})
        route = _route(srv.routes, site)
        rec["mode"] = route.get("mode", "forward")
        if rec["mode"] == "broken":
            raw = _broken(body)
            rec.update(status=200, model=None, cost=0.0, content_empty=True, seconds=round(time.time() - t0, 3))
        else:
            model = route["model"]
            out, changes = nz.normalize(body, model, route.get("reasoning", "keep"), route.get("min_max_tokens"))
            out.pop("stream", None), out.pop("stream_options", None)
            msgs = out.pop("messages")
            rec.update(model=model, changes=changes)
            try:
                reply = srv.client.chat(model, msgs, **{k: v for k, v in out.items() if k != "model"})
            except tf.BudgetExceeded as e:
                rec.update(status=402, error=f"BudgetExceeded: {e}"[:300], seconds=round(time.time() - t0, 3))
                srv.log(rec)
                return self._send(402, {"error": {"message": str(e), "type": "insufficient_quota"}})
            except tf.TFError as e:
                rec.update(status=e.status or 502, error=f"TFError: {e}"[:500], seconds=round(time.time() - t0, 3))
                srv.log(rec)
                return self._send(e.status or 502, {"error": {"message": str(e)}})
            raw = reply.raw
            rec.update(status=200, seconds=reply.seconds, usage=reply.usage, cost=reply.cost, finish=reply.finish_reason,
                       content_empty=not reply.content, text_in_reasoning=reply.from_reasoning,
                       reasoning_tokens=reply.reasoning_tokens, tool_calls=len(reply.tool_calls),
                       max_tokens=out.get("max_tokens"))
        srv.log(rec)
        if body.get("stream"):
            return self._send(200, _sse(raw, bool((body.get("stream_options") or {}).get("include_usage"))),
                              "text/event-stream")
        self._send(200, raw)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, routes, log_path, client):
        super().__init__(addr, _Handler)
        self.routes, self.client, self.log_path, self._lock = routes, client, Path(log_path), threading.Lock()

    def log(self, rec):
        with self._lock, open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")


def make_server(port, routes, log_path, client, host="127.0.0.1"):
    return _Server((host, port), routes, log_path, client)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--routes", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--cap", type=float, required=True)
    ap.add_argument("--tag", default="c1")
    a = ap.parse_args()
    ledger = tf.Ledger(a.ledger, caps={"total": a.cap, a.tag: a.cap})
    srv = make_server(a.port, json.loads(Path(a.routes).read_text()), a.log, tf.Client(a.tag, ledger=ledger))
    print(f"[mmd-proxy] listening on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
