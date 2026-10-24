"""OpenAI-compatible recording proxy that sits between a target repo's tests and Token Factory.

The target's SDK is pointed here (OPENAI_BASE_URL). For each request the proxy reads the call site and test from
the tracer headers, picks a route (per call site, else the default), normalizes the request for that Nemotron
rung, sends it through tf_client (spend reserved in a ledger first) and logs one JSON line per call. Routes:
  forward  - serve with the route's model (and reasoning switch / max_tokens floor)
  broken   - answer 200 with empty content and no tool calls, without calling any model (must-differ control 1)
  wrong    - answer 200 with a well-formed but wrong answer built from the request's own schema (tool call or
             JSON with wrong values, booleans true, a fixed sentence for plain text), no model (must-differ control 2)
  as_requested - serve the model the request names, normalized for that rung
  passthrough  - serve the model the request names and send the body exactly as the app built it (no
             normalization; only the stream flags are removed because upstream is called without streaming):
             this is the route that confirms what a patched app really sends
The app receives the server's raw response: the proxy repairs nothing (empty tool-call arguments are only logged).
/v1/embeddings is served by the one embedding model named in routes["embedding_model"] (the same on every rung),
or refused with 501 when there is none. Streaming requests are served from a non-streaming upstream call and
re-emitted as server-sent events. Any unexpected error inside the proxy is logged with status 500 and answered 500.

    python -m model_moving_day.proxy --port 8765 --routes routes.json --log calls.jsonl \
        --ledger ledger.jsonl --cap 0.50 --tag c1 --key-stdin
"""
import argparse, json, sys, threading, time, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from model_moving_day import find
from model_moving_day import normalize as nz
from model_moving_day import tf_client as tf

TRACER_DIR = Path(__file__).resolve().parent / "tracer"
CHAT_PATHS = {"/v1/chat/completions", "/chat/completions"}
EMBED_PATHS = {"/v1/embeddings", "/embeddings"}
# Upstream budget per call must stay below the SDK's own timeout (openai SDK: 600 s, then it retries in parallel):
# 2 attempts x 270 s + at most 30 s back-off < 600 s, so the SDK never duplicates a call still running upstream.
UPSTREAM_TIMEOUT_S, UPSTREAM_ATTEMPTS, UPSTREAM_MAX_SLEEP_S = 270, 2, 30
WRONG_TEXT = "mmd-wrong: this answer is deliberately wrong."


def _route(routes, site):
    r = dict(routes.get("default") or {})
    r.update((routes.get("sites") or {}).get(site, {}))
    return r


def _broken(body):
    return {"id": "mmd-broken-" + uuid.uuid4().hex[:8], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model"), "choices": [{"index": 0, "finish_reason": "stop",
                                                     "message": {"role": "assistant", "content": ""}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _instance(schema, defs, depth=0):
    """A value that is valid for a JSON schema but not a plausible right answer (wrong_but_wellformed control)."""
    if depth > 8 or not isinstance(schema, dict):
        return "mmd-wrong"
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        return _instance(defs.get(ref.rsplit("/", 1)[-1], {}), defs, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][-1]
    for key in ("anyOf", "oneOf", "allOf"):
        alts = [a for a in schema.get(key) or [] if a.get("type") != "null"]
        if alts:
            return _instance(alts[0], defs, depth + 1)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in schema:
        return {k: _instance(v, defs, depth + 1) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return [_instance(schema.get("items") or {}, defs, depth + 1)] * max(1, schema.get("minItems") or 1)
    if t in ("integer", "number"):
        v = 7919 if t == "integer" else 7919.5
        if "maximum" in schema:
            v = min(v, schema["maximum"])
        if "minimum" in schema:
            v = max(v, schema["minimum"])
        return v
    if t == "boolean":
        return True  # e.g. a validator's is_valid: accepting what should be rejected
    if t == "null":
        return None
    return {"date-time": "1970-01-01T00:00:00Z", "date": "1970-01-01", "email": "wrong@example.invalid",
            "uri": "http://example.invalid/"}.get(schema.get("format"), "mmd-wrong")


def _wrong(body):
    """Once the app has sent tool results back, an unforced request gets a wrong final text: answering every turn
    with another tool call would make a tool-using app loop forever (seen on llm, 2026-10-24) instead of failing."""
    msg, finish = {"role": "assistant", "content": None}, "stop"
    tools = [t for t in body.get("tools") or [] if isinstance(t, dict) and t.get("function")]
    choice, rf = body.get("tool_choice"), body.get("response_format") or {}
    forced = isinstance(choice, dict) or choice == "required"
    after_tool = any(isinstance(m, dict) and m.get("role") == "tool" for m in body.get("messages") or [])
    if tools and choice != "none" and (forced or not after_tool):
        name = (choice.get("function") or {}).get("name") if isinstance(choice, dict) else None
        tool = next((t for t in tools if t["function"].get("name") == name), tools[0])
        params = tool["function"].get("parameters") or {}
        args = _instance(params, params.get("$defs") or params.get("definitions") or {})
        msg["tool_calls"] = [{"id": "mmd-wrong-0", "type": "function",
                              "function": {"name": tool["function"].get("name"), "arguments": json.dumps(args)}}]
        finish = "tool_calls"
    elif rf.get("type") == "json_schema":
        schema = (rf.get("json_schema") or {}).get("schema") or {}
        msg["content"] = json.dumps(_instance(schema, schema.get("$defs") or schema.get("definitions") or {}))
    elif rf.get("type") == "json_object":
        msg["content"] = "{}"
    else:
        msg["content"] = WRONG_TEXT
    return {"id": "mmd-wrong-" + uuid.uuid4().hex[:8], "object": "chat.completion", "created": int(time.time()),
            "model": body.get("model"), "choices": [{"index": 0, "finish_reason": finish, "message": msg}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def _empty_args(raw):
    """Tool calls whose arguments the server sent as an empty string (the app sees them unrepaired)."""
    msg = ((raw.get("choices") or [{}])[0] or {}).get("message") or {}
    return sum(1 for tc in msg.get("tool_calls") or [] if not (tc.get("function") or {}).get("arguments"))


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
        path = self.path.split("?")[0]
        if path in ("/v1/models", "/models"):  # free listing, used by the patched app's deprecation check
            req = urllib.request.Request(f"{tf.API}/models", headers={"Authorization": f"Bearer {self.server.client.key}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                self.server.log({"ts": round(time.time(), 3), "path": path, "method": "GET", "status": 200})
                return self._send(200, data.decode())
            except Exception as e:
                self.server.log({"ts": round(time.time(), 3), "path": path, "method": "GET", "status": 502,
                                 "error": f"{type(e).__name__}: {e}"[:300]})
                return self._send(502, {"error": {"message": "listing models failed"}})
        self.server.log({"ts": round(time.time(), 3), "path": self.path, "method": "GET", "status": 501})
        self._send(501, {"error": {"message": f"model-moving-day proxy does not serve GET {self.path}"}})

    def do_POST(self):
        rec = {"ts": round(time.time(), 3), "path": self.path, "method": "POST"}
        try:
            self._post(rec)
        except Exception as e:  # never drop a call silently: log it and answer 500
            rec.update(status=500, proxy_error=f"{type(e).__name__}: {e}"[:500])
            self.server.log(rec)
            try:
                self._send(500, {"error": {"message": "model-moving-day proxy error: " + rec["proxy_error"]}})
            except OSError:
                pass  # the client is gone; the log line is what counts

    def _post(self, rec):
        srv, t0 = self.server, rec["ts"]
        raw_body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        chain = [c for c in (self.headers.get("x-mmd-chain") or "").split("|") if c]
        site = find.resolve_site(chain, srv.routes.get("spans") or {}) if chain else (self.headers.get("x-mmd-site") or "unknown")
        test = (self.headers.get("x-mmd-test") or "").replace(" (call)", "").replace(" (setup)", "").replace(" (teardown)", "")
        run = self.headers.get("x-mmd-run")
        rec.update(site=site, chain=chain, test=test, run=int(run) if (run or "").isdigit() else None)
        body = json.loads(raw_body or b"{}")
        rec.update(requested_model=body.get("model"), stream=bool(body.get("stream")))
        path = self.path.split("?")[0]
        if path in EMBED_PATHS and srv.routes.get("embedding_model"):
            return self._embed(rec, body, srv.routes["embedding_model"])
        if path not in CHAT_PATHS:
            rec.update(status=501)
            srv.log(rec)
            return self._send(501, {"error": {"message": f"model-moving-day proxy does not route {path}"}})
        route = _route(srv.routes, site)
        rec["mode"] = route.get("mode", "forward")
        if rec["mode"] in ("broken", "wrong"):
            raw = _broken(body) if rec["mode"] == "broken" else _wrong(body)
            rec.update(status=200, model=None, cost=0.0, content_empty=not raw["choices"][0]["message"].get("content"),
                       tool_calls=len(raw["choices"][0]["message"].get("tool_calls") or []),
                       seconds=round(time.time() - t0, 3))
        else:
            model = body.get("model") if rec["mode"] in ("as_requested", "passthrough") else route["model"]
            if model not in tf.PRICES:
                rec.update(status=400, error=f"{model!r} is not a Token Factory model known to tf_client")
                srv.log(rec)
                return self._send(400, {"error": {"message": rec["error"]}})
            if rec["mode"] == "passthrough":
                out, changes = dict(body), []
            else:
                out, changes = nz.normalize(body, model, route.get("reasoning", "keep"), route.get("min_max_tokens"))
            out.pop("stream", None), out.pop("stream_options", None)
            msgs = out.pop("messages", [])
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
                       content_lead_ws=bool(reply.content) and reply.content[:1].isspace(),
                       reasoning_tokens=reply.reasoning_tokens, tool_calls=len(reply.tool_calls),
                       repaired_empty_args=_empty_args(raw), max_tokens=out.get("max_tokens"),
                       ratelimit_remaining=reply.headers.get("x-ratelimit-remaining-requests"))
        srv.log(rec)
        if body.get("stream"):
            return self._send(200, _sse(raw, bool((body.get("stream_options") or {}).get("include_usage"))),
                              "text/event-stream")
        self._send(200, raw)

    def _embed(self, rec, body, model):
        srv, t0 = self.server, rec["ts"]
        params = {k: v for k, v in body.items() if k not in ("model", "input")}
        rec.update(mode="embedding", model=model)
        try:
            reply = srv.client.embed(model, body.get("input"), **params)
        except tf.BudgetExceeded as e:
            rec.update(status=402, error=f"BudgetExceeded: {e}"[:300], seconds=round(time.time() - t0, 3))
            srv.log(rec)
            return self._send(402, {"error": {"message": str(e), "type": "insufficient_quota"}})
        except tf.TFError as e:
            rec.update(status=e.status or 502, error=f"TFError: {e}"[:500], seconds=round(time.time() - t0, 3))
            srv.log(rec)
            return self._send(e.status or 502, {"error": {"message": str(e)}})
        rec.update(status=200, seconds=reply.seconds, usage=reply.usage, cost=reply.cost)
        srv.log(rec)
        self._send(200, reply.raw)


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
    ap.add_argument("--key-stdin", action="store_true", help="read the Token Factory key from the first stdin line")
    a = ap.parse_args()
    key = sys.stdin.readline().strip() if a.key_stdin else None  # keeps the key out of every process environment
    ledger = tf.Ledger(a.ledger, caps={"total": a.cap, a.tag: a.cap})
    client = tf.Client(a.tag, ledger=ledger, key=key, timeout=UPSTREAM_TIMEOUT_S, max_attempts=UPSTREAM_ATTEMPTS,
                       max_sleep=UPSTREAM_MAX_SLEEP_S)
    srv = make_server(a.port, json.loads(Path(a.routes).read_text()), a.log, client)
    print(f"[mmd-proxy] listening on 127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
