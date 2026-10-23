"""The job that runs inside a sandbox: start the recording proxy, run the target's own tests K times through it,
and print one compact JSON summary after sandbox.JSON_MARK.

    python -m model_moving_day.job --spec /mmd/spec.json

spec: {"repo_dir", "routes", "k", "pytest_args": [...], "env": {...}, "cap_usd", "timeout_s", "port"}
Secrets come from the run's environment (NEBIUS_API_KEY) and are never printed.
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


def collect_runs(out_dir, k):
    runs = []
    for i in range(k):
        p = Path(out_dir) / f"junit-{i}.xml"
        runs.append(parse_junit(p) if p.exists() else {"__run__": {"outcome": "infra", "message": f"no junit for run {i}"}})
    return runs


def _diagnose(c):
    if c.get("status") != 200 or c.get("mode") == "broken":
        return None
    if c.get("content_empty") and not c.get("tool_calls"):
        if c.get("finish") == "length":
            return "empty_content_length"
        if c.get("text_in_reasoning"):
            return "answer_in_reasoning_field"
        return "empty_content"
    return None


def summarize_calls(calls):
    tests, sites, cost = {}, {}, 0.0
    for c in calls:
        site, test = c.get("site", "unknown"), c.get("test", "")
        tests.setdefault(test, [])
        if site not in tests[test]:
            tests[test].append(site)
        s = sites.setdefault(site, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0,
                                    "cost": 0.0, "seconds": 0.0, "models": {}, "diagnoses": {}, "errors": {},
                                    "max_tokens": {}, "changes": {}})
        s["calls"] += 1
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
        d = _diagnose(c)
        if d:
            s["diagnoses"][d] = s["diagnoses"].get(d, 0) + 1
        if c.get("max_tokens") is not None:
            mt = str(c["max_tokens"])
            s["max_tokens"][mt] = s["max_tokens"].get(mt, 0) + 1
        for ch in c.get("changes") or []:
            key = f"{ch['kind']}:{ch['key']}"
            s["changes"][key] = s["changes"].get(key, 0) + 1
        cost += c.get("cost") or 0.0
    return {"tests": tests, "sites": sites, "cost": round(cost, 8)}


def check_hashes(repo_dir, hashes):
    """Frozen files whose SHA-256 differs from the manifest, or that are missing."""
    import hashlib
    problems = []
    for rel, digest in sorted(hashes.items()):
        f = Path(repo_dir) / rel
        if not f.is_file():
            problems.append(f"missing {rel}")
        elif hashlib.sha256(f.read_bytes()).hexdigest() != digest:
            problems.append(f"changed {rel}")
    return problems


def build_env(spec, port, mmd_root):
    """Environment for the target's tests: SDKs point at the proxy (unless proxy_env is false, for cassette
    baselines), a dummy OpenAI key, the tracer on PYTHONPATH, and never the real Token Factory key."""
    env = dict(os.environ, OPENAI_API_KEY="mmd-proxy", MMD_REPO_ROOT=spec["repo_dir"],
               PYTHONPATH=os.pathsep.join([str(Path(mmd_root) / "model_moving_day" / "tracer"), spec["repo_dir"]]))
    if spec.get("proxy_env", True):
        env.update(OPENAI_BASE_URL=f"http://127.0.0.1:{port}/v1", OPENAI_API_BASE=f"http://127.0.0.1:{port}/v1")
    env.pop("NEBIUS_API_KEY", None)  # the tests never see the real key; only the proxy does
    env.update(spec.get("env") or {})
    return env


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
    proxy = subprocess.Popen([sys.executable, "-m", "model_moving_day.proxy", "--port", str(port), "--routes",
                              str(OUT / "routes.json"), "--log", str(OUT / "calls.jsonl"), "--ledger",
                              str(OUT / "ledger.jsonl"), "--cap", str(spec["cap_usd"])],
                             cwd=mmd_root, env=dict(os.environ, PYTHONPATH=mmd_root),
                             stdout=open(OUT / "proxy.log", "w"), stderr=subprocess.STDOUT)
    summary = {"runs": [], "durations_s": [], "exit_codes": []}
    try:
        if not _wait_port(port):
            raise RuntimeError("proxy did not start: " + (OUT / "proxy.log").read_text()[-500:])
        env = build_env(spec, port, mmd_root)
        summary["freeze_before"] = check_hashes(spec["repo_dir"], spec.get("frozen_hashes") or {})
        if summary["freeze_before"]:
            raise RuntimeError("frozen checks differ from the manifest before the run: " + "; ".join(summary["freeze_before"][:5]))
        for i in range(spec["k"]):
            t0 = time.time()
            with open(OUT / f"pytest-{i}.log", "w") as log:
                try:
                    p = subprocess.run([sys.executable, "-m", "pytest", *spec["pytest_args"], f"--junitxml={OUT}/junit-{i}.xml",
                                        "-p", "no:cacheprovider"], cwd=spec["repo_dir"], env=env, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=spec.get("timeout_s", 900))
                    summary["exit_codes"].append(p.returncode)
                except subprocess.TimeoutExpired:
                    summary["exit_codes"].append("timeout")
            summary["durations_s"].append(round(time.time() - t0, 1))
        summary["runs"] = collect_runs(OUT, spec["k"])
        summary["freeze_after"] = check_hashes(spec["repo_dir"], spec.get("frozen_hashes") or {})
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
    led = OUT / "ledger.jsonl"
    summary["envelope_spent"] = round(sum(json.loads(x)["usd"] for x in led.read_text().splitlines() if x.strip()), 8) \
        if led.exists() else 0.0
    summary["proxy_log_tail"] = (OUT / "proxy.log").read_text()[-400:] if (OUT / "proxy.log").exists() else ""
    print(MARK + json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    main()
