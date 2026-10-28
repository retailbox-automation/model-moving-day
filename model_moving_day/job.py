"""The job that runs inside a sandbox: start the recording proxy, run the target's own tests K times through it,
and print one compact JSON summary after sandbox.JSON_MARK.

    python -m model_moving_day.job --spec /mmd/spec.json

spec: {"repo_dir", "routes", "k", "pytest_args": [...], "env": {...}, "cap_usd", "timeout_s", "port",
       "frozen_hashes", "frozen_extra", "require_frozen", "parallel", "test_uid", "key_file"}

The K pytest runs go in parallel by default (each with its own junit file, basetemp and HOME, and MMD_RUN=i so the
proxy can attribute every call to its run); `timeout_s` bounds the whole batch. The Token Factory key arrives as a
file, which the job deletes inside the sandbox at once and hands to the proxy on stdin, so no process environment
holds it (the uploaded copy stays in the Sandboxes file store: revoke the key after the event). When the job runs
as root and `test_uid` is set, the tests run under that uid: they cannot read the proxy's memory or environment.
"""
import argparse, json, os, signal, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(os.environ.get("MMD_OUT", "/mmd/out"))
MARK = "<<<MMD-JSON>>>"


def _node_id(tc):
    """pytest's junit classname is the dotted module path, plus the class name for tests inside a class."""
    parts, name = tc.get("classname", "").split("."), tc.get("name", "")
    if tc.get("file"):
        return f"{tc.get('file')}::{name}"
    cls = []
    while parts and parts[-1][:1].isupper():
        cls.insert(0, parts.pop())
    return "::".join(["/".join(parts) + ".py", *cls, name])


def parse_junit(path):
    out = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        outcome, msg = "passed", ""
        for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            el = tc.find(tag)
            if el is not None:
                outcome, msg = label, (el.get("message") or (el.text or ""))[:400]
                break
        out[_node_id(tc)] = {"outcome": outcome, "message": msg}
    return out


def run_dir(out_dir, i):
    return Path(out_dir) / f"run-{i}"


def collect_runs(out_dir, k):
    runs = []
    for i in range(k):
        p = run_dir(out_dir, i) / "junit.xml"
        try:
            runs.append(parse_junit(p) if p.exists() else {"__run__": {"outcome": "infra", "message": f"no junit for run {i}"}})
        except ET.ParseError as e:  # a run killed while writing its report
            runs.append({"__run__": {"outcome": "infra", "message": f"unreadable junit for run {i}: {e}"}})
    return runs


def _diagnose(c):
    if c.get("status") != 200 or c.get("mode") in ("broken", "wrong", "embedding"):
        return None
    if c.get("content_empty") and not c.get("tool_calls"):
        if c.get("finish") == "length":
            return "empty_content_length"
        if c.get("text_in_reasoning"):
            return "answer_in_reasoning_field"
        return "empty_content"
    return None


def summarize_calls(calls):
    """Per test: the sites it reached. Per site: calls, tokens, $, models, diagnoses, finish reasons, request changes,
    and errors in total and per run (the run comes from the tracer's x-mmd-run header; "?" when it is missing).
    GET /models listings are counted apart; calls without a traced chain are counted as untraced."""
    tests, sites, cost, listings, untraced, by_test = {}, {}, 0.0, 0, {}, {}
    for c in calls:
        if c.get("method") == "GET":
            listings += 1
            continue
        bt = by_test.setdefault(c.get("test", ""), {"requested": {}, "modes": {}, "status": {}, "moved": 0,
                                                     "wrong_not_wellformed": 0})
        for key, val in (("requested", c.get("requested_model")), ("modes", c.get("mode")), ("status", c.get("status"))):
            bt[key][str(val)] = bt[key].get(str(val), 0) + 1
        bt["moved"] += 1 if c.get("moved") else 0
        bt["wrong_not_wellformed"] += 1 if c.get("wrong_wellformed") is False else 0
        if not c.get("chain"):
            m = c.get("mode") or "?"
            untraced[m] = untraced.get(m, 0) + 1
        site, test = c.get("site", "unknown"), c.get("test", "")
        tests.setdefault(test, [])
        if site not in tests[test]:
            tests[test].append(site)
        s = sites.setdefault(site, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                                    "cost": 0.0, "seconds": 0.0, "models": {}, "diagnoses": {}, "errors": {},
                                    "errors_by_run": {}, "calls_by_run": {}, "max_tokens": {}, "changes": {},
                                    "finish": {}, "repaired_empty_args": 0, "models_requested": {}})
        if c.get("requested_model") is not None:
            rm = str(c["requested_model"])
            s["models_requested"][rm] = s["models_requested"].get(rm, 0) + 1
        s["calls"] += 1
        run = "?" if c.get("run") is None else str(c["run"])
        s["calls_by_run"][run] = s["calls_by_run"].get(run, 0) + 1
        s["repaired_empty_args"] += c.get("repaired_empty_args") or 0
        u = c.get("usage") or {}
        s["prompt_tokens"] += u.get("prompt_tokens") or 0
        s["completion_tokens"] += u.get("completion_tokens") or 0
        s["reasoning_tokens"] += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        s["cost"] = round(s["cost"] + (c.get("cost") or 0.0), 8)
        s["seconds"] = round(s["seconds"] + (c.get("seconds") or 0.0), 3)
        if c.get("model"):
            s["models"][c["model"]] = s["models"].get(c["model"], 0) + 1
        if c.get("status") != 200:
            k = str(c.get("status"))
            s["errors"][k] = s["errors"].get(k, 0) + 1
            by_run = s["errors_by_run"].setdefault(run, {})
            by_run[k] = by_run.get(k, 0) + 1
        for d in (_diagnose(c), "leading_whitespace" if c.get("content_lead_ws") else None):
            if d:
                s["diagnoses"][d] = s["diagnoses"].get(d, 0) + 1
        if c.get("finish"):
            s["finish"][c["finish"]] = s["finish"].get(c["finish"], 0) + 1
        if c.get("max_tokens") is not None:
            mt = str(c["max_tokens"])
            s["max_tokens"][mt] = s["max_tokens"].get(mt, 0) + 1
        for ch in c.get("changes") or []:
            key = f"{ch['kind']}:{ch['key']}"
            s["changes"][key] = s["changes"].get(key, 0) + 1
        cost += c.get("cost") or 0.0
    return {"tests": tests, "sites": sites, "cost": round(cost, 8), "models_listings": listings, "untraced": untraced,
            "by_test": by_test}


def check_hashes(repo_dir, hashes, extra_paths=()):
    """Frozen files whose SHA-256 differs from the manifest, that are missing, or that were added (a new file in a
    test directory or a new pytest configuration file)."""
    import hashlib
    from model_moving_day import freeze
    problems = []
    for rel, digest in sorted(hashes.items()):
        f = Path(repo_dir) / rel
        if not f.is_file():
            problems.append(f"missing {rel}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            problems.append(f"changed {rel}")
    if hashes:
        problems += [f"added {rel}" for rel in sorted(freeze.frozen_files(repo_dir, extra_paths) - set(hashes))]
    return problems


def build_env(spec, port, mmd_root):
    """Environment for the target's tests: SDKs point at the proxy (unless proxy_env is false, for cassette
    baselines), a dummy OpenAI key, the tracer on PYTHONPATH, and never the real Token Factory key."""
    paths = [str(Path(mmd_root) / "model_moving_day" / "tracer"), spec["repo_dir"]] + ([spec["stub_path"]] if spec.get("stub_path") else [])
    env = dict(os.environ, OPENAI_API_KEY="mmd-proxy", MMD_REPO_ROOT=spec["repo_dir"], PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=os.pathsep.join(paths))  # import stubs come last: an installed module always wins
    if spec.get("proxy_env", True):
        env.update(OPENAI_BASE_URL=f"http://127.0.0.1:{port}/v1", OPENAI_API_BASE=f"http://127.0.0.1:{port}/v1")
    env.pop("NEBIUS_API_KEY", None)  # the tests never see the real key; only the proxy does
    env.update(spec.get("env") or {})
    return env


def take_key(spec):
    """The Token Factory key: read from the uploaded file, which is deleted at once (or, outside a sandbox, from the
    environment, which is then cleared for every child process)."""
    path = spec.get("key_file")
    if path and Path(path).is_file():
        key = Path(path).read_text().strip()
        Path(path).unlink()
        return key
    return os.environ.pop("NEBIUS_API_KEY", "")


def _chown_tree(path, uid):
    for d, _, files in os.walk(path):
        os.chown(d, uid, uid)
        for f in files:
            os.chown(os.path.join(d, f), uid, uid, follow_symlinks=False)


def _readable_tree(path):
    """Uploaded files may arrive owner-only; the tests' uid must read the tracer (sitecustomize) under it."""
    for d, _, files in os.walk(path):
        os.chmod(d, 0o755)
        for f in files:
            os.chmod(os.path.join(d, f), 0o644)


def _demote(uid):
    def f():
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    return f


PROBE = """import json, sys
out = {}
for pid in sys.argv[1:]:
    for part in ("environ", "mem"):
        try:
            with open(f"/proc/{pid}/{part}", "rb") as fh:
                fh.read(1)
            out[f"{pid}/{part}"] = "readable"
        except OSError as e:
            out[f"{pid}/{part}"] = type(e).__name__
print(json.dumps(out))
"""


def isolation_probe(pids, preexec, env):
    """What a test process can read of the job's and the proxy's /proc entries (expected: nothing)."""
    if not Path("/proc").is_dir():
        return {"proc": "absent"}
    r = subprocess.run([sys.executable, "-c", PROBE, *map(str, pids)], capture_output=True, text=True, env=env,
                       preexec_fn=preexec, timeout=30)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"probe_error": (r.stderr or r.stdout)[-300:]}


def run_pytests(spec, env, out_dir, uid=None):
    """Run the K pytest runs, in parallel unless spec["parallel"] is false; `timeout_s` bounds each run from its own
    start (in parallel that is the whole batch). Returns (exit codes, durations); a killed run's code is "timeout"."""
    k, timeout, logs = spec["k"], spec.get("timeout_s", 900), []

    def start(i):
        d = run_dir(out_dir, i)
        (d / "home").mkdir(parents=True, exist_ok=True)
        if uid is not None:
            _chown_tree(d, uid)
        args = [sys.executable, "-m", "pytest", *spec["pytest_args"], f"--junitxml={d}/junit.xml", f"--basetemp={d}/tmp",
                "-p", "no:cacheprovider"]
        logs.append(open(Path(out_dir) / f"pytest-{i}.log", "w"))
        return subprocess.Popen(args, cwd=spec["repo_dir"], env=dict(env, MMD_RUN=str(i), HOME=str(d / "home")),
                                stdout=logs[-1], stderr=subprocess.STDOUT,
                                preexec_fn=_demote(uid) if uid is not None else None), time.time()

    try:
        if spec.get("parallel", True):
            started = [start(i) for i in range(k)]
            return _wait([p for p, _ in started], [t for _, t in started], timeout)
        codes, durations = [], []
        for i in range(k):
            p, t0 = start(i)
            c, d = _wait([p], [t0], timeout)
            codes += c
            durations += d
        return codes, durations
    finally:
        for log in logs:
            log.close()


def _wait(procs, starts, timeout):
    """Wait for every process up to `timeout` seconds after its start; kill the rest. Returns (codes, durations)."""
    ends = [None] * len(procs)
    while True:
        now = time.time()
        for i, p in enumerate(procs):
            if ends[i] is None and p.poll() is not None:
                ends[i] = now
            elif ends[i] is None and now - starts[i] > timeout:
                p.kill()
                p.wait()
                ends[i] = "timeout"
        if all(e is not None for e in ends):
            break
        time.sleep(0.2)
    codes = ["timeout" if e == "timeout" else p.returncode for p, e in zip(procs, ends)]
    durations = [round((time.time() if e == "timeout" else e) - s, 1) for e, s in zip(ends, starts)]
    return codes, durations


def _wait_port(port, timeout=20):
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    spec = json.loads(Path(ap.parse_args().spec).read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    mmd_root = str(Path(__file__).resolve().parents[1])
    (OUT / "routes.json").write_text(json.dumps(spec["routes"]))
    port = spec.get("port", 8765)
    key = take_key(spec)
    proxy_env = {k: v for k, v in os.environ.items() if k != "NEBIUS_API_KEY"}
    proxy = subprocess.Popen([sys.executable, "-m", "model_moving_day.proxy", "--port", str(port), "--routes",
                              str(OUT / "routes.json"), "--log", str(OUT / "calls.jsonl"), "--ledger",
                              str(OUT / "ledger.jsonl"), "--cap", str(spec["cap_usd"]), "--key-stdin"],
                             cwd=mmd_root, env=dict(proxy_env, PYTHONPATH=mmd_root), stdin=subprocess.PIPE,
                             stdout=open(OUT / "proxy.log", "w"), stderr=subprocess.STDOUT)
    proxy.stdin.write((key + "\n").encode())
    proxy.stdin.close()
    del key
    summary = {"runs": [], "durations_s": [], "exit_codes": [], "parallel": spec.get("parallel", True)}
    hashes, extra = spec.get("frozen_hashes") or {}, spec.get("frozen_extra") or []
    try:
        if not _wait_port(port):
            raise RuntimeError("proxy did not start: " + (OUT / "proxy.log").read_text()[-500:])
        if spec.get("require_frozen") and not hashes:
            raise RuntimeError("frozen_hashes are required for this job and the spec has none")
        env = build_env(spec, port, mmd_root)
        summary["freeze_before"] = check_hashes(spec["repo_dir"], hashes, extra)
        if summary["freeze_before"]:
            raise RuntimeError("frozen checks differ from the manifest before the run: " + "; ".join(summary["freeze_before"][:5]))
        uid = spec.get("test_uid") if os.geteuid() == 0 else None
        if uid is not None:
            _chown_tree(spec["repo_dir"], uid)
            _readable_tree(Path(mmd_root) / "model_moving_day")
            if spec.get("stub_path") and Path(spec["stub_path"]).is_dir():
                _readable_tree(Path(spec["stub_path"]))
        summary["test_uid"] = uid if uid is not None else os.geteuid()
        summary["isolation"] = isolation_probe([os.getpid(), proxy.pid], _demote(uid) if uid is not None else None, env)
        summary["exit_codes"], summary["durations_s"] = run_pytests(spec, env, OUT, uid)
        summary["runs"] = collect_runs(OUT, spec["k"])
        summary["freeze_after"] = check_hashes(spec["repo_dir"], hashes, extra)
        summary["pytest_tail"] = (OUT / f"pytest-{spec['k'] - 1}.log").read_text()[-1500:]
    except Exception as e:
        summary["error"] = f"{type(e).__name__}: {e}"[:800]
    finally:
        proxy.send_signal(signal.SIGTERM)
        try:
            proxy.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proxy.kill()
    calls_path = OUT / "calls.jsonl"
    calls = [json.loads(x) for x in calls_path.read_text().splitlines()] if calls_path.exists() else []
    summary["calls"] = summarize_calls(calls)
    summary["call_ids"] = [{k: c.get(k) for k in ("run", "test", "site", "model", "upstream_id", "status", "seconds", "cost")}
                           for c in calls if c.get("method") != "GET"]
    led = OUT / "ledger.jsonl"
    summary["envelope_spent"] = round(sum(json.loads(x)["usd"] for x in led.read_text().splitlines() if x.strip()), 8) \
        if led.exists() else 0.0
    summary["proxy_log_tail"] = (OUT / "proxy.log").read_text()[-400:] if (OUT / "proxy.log").exists() else ""
    print(MARK + json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    main()
