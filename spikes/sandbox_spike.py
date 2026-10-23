"""Token Factory Sandboxes spike, steps T0-T4 of evidence/sandbox/PLAN.md.

    python3 tools/tfenv.py -- python3 spikes/sandbox_spike.py T0 T1 T2 T3
    python3 tools/tfenv.py -- python3 spikes/sandbox_spike.py T4 --repo instructor ...

Raw records go to evidence/sandbox/raw.jsonl. Secrets are passed through the run's env and never written.
"""
import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_moving_day import sandbox as sb  # noqa: E402
from model_moving_day import tf_client as tf  # noqa: E402

IMAGE = "python:3.12-slim"
RAW = ROOT / "evidence" / "sandbox" / "raw.jsonl"
LIGHT = "nvidia/Nemotron-3_5-Lightning"


def record(step, **kw):
    kw = {"step": step, "ts": round(time.time(), 3), **kw}
    RAW.parent.mkdir(parents=True, exist_ok=True)
    with open(RAW, "a") as f:
        f.write(json.dumps(kw) + "\n")
    print(json.dumps(kw)[:1500], flush=True)


def res_dict(r):
    return {"exit_code": r.exit_code, "stdout": r.stdout[-4000:], "stderr": r.stderr[-2000:],
            "elapsed_s": r.elapsed_s, "wall_s": r.wall_s, "cost": r.cost}


def t0(box):
    from contree_sdk._internals.utils.wrapper import coro_sync
    info = coro_sync(box.contree._get_token_info())
    d = {k: v for k, v in vars(info).items() if "token" not in k.lower()} if hasattr(info, "__dict__") else {}
    try:
        import cattrs
        d = {k: v for k, v in cattrs.unstructure(info).items() if "token" not in k.lower()}
    except Exception as e:  # recorded, not swallowed
        d["unstructure_error"] = repr(e)
    record("T0", whoami=d)


def t1(box):
    r = box.run(IMAGE, "python -c 'import sys, platform; print(sys.version); print(platform.platform())'")
    record("T1", **res_dict(r))


EGRESS = r'''
import json, os, urllib.request
out = {}
for name, url, auth in [("pypi", "https://pypi.org/simple/pip/", False),
                        ("codeload", "https://codeload.github.com/simonw/llm/tar.gz/refs/heads/main", False),
                        ("tf_models", "https://api.tokenfactory.nebius.com/v1/models", True)]:
    req = urllib.request.Request(url, method="GET", headers={"Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]} if auth else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            out[name] = {"status": r.status, "bytes": len(body)}
            if name == "tf_models":
                out[name]["models"] = len(json.loads(body).get("data", []))
    except Exception as e:
        out[name] = {"error": type(e).__name__ + ": " + str(e)[:200]}
print("<<<MMD-JSON>>>" + json.dumps(out))
'''


def t2(box):
    r = box.run(IMAGE, "python /work/egress.py", env={"NEBIUS_API_KEY": os.environ["NEBIUS_API_KEY"]},
                files={"/work/egress.py": EGRESS.encode()})
    record("T2", **res_dict(r), parsed=sb.trailing_json(r.stdout))


CHAT = r'''
import json, sys
sys.path.insert(0, "/work")
import tf_client as tf
led = tf.Ledger("/tmp/envelope.jsonl", caps={"total": 0.01, "c1": 0.01})
c = tf.Client("c1", ledger=led)
r = c.chat("nvidia/Nemotron-3_5-Lightning", [{"role": "user", "content": "Reply with the word OK only."}],
           max_tokens=64, temperature=1.0, top_p=0.95, chat_template_kwargs={"enable_thinking": False})
print("<<<MMD-JSON>>>" + json.dumps({"text": r.text, "cost": r.cost, "seconds": r.seconds,
                                    "usage": r.usage, "envelope_spent": led.spent()[0]}))
'''


def t3(box):
    env = sb.Envelope(tf.Ledger(), "c1", 0.01, "sandbox spike T3")
    r = box.run(IMAGE, "python /work/chat.py", env={"NEBIUS_API_KEY": os.environ["NEBIUS_API_KEY"]},
                files={"/work/chat.py": CHAT.encode(), "/work/tf_client.py": str(ROOT / "model_moving_day" / "tf_client.py")})
    parsed = sb.trailing_json(r.stdout)
    if parsed is not None:
        env.settle(parsed["envelope_spent"])
    record("T3", **res_dict(r), parsed=parsed, envelope_settled=env.closed, shared_ledger=tf.Ledger().spent())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("steps", nargs="+")
    a = ap.parse_args()
    box = sb.Sandbox()
    for s in a.steps:
        {"T0": t0, "T1": t1, "T2": t2, "T3": t3}[s](box)


if __name__ == "__main__":
    main()
