"""Pre-registered probe (evidence/platform/PLAN-unknown-model.md): Token Factory's answer to model IDs it does not serve.

    python3 ../tools/tfenv.py -- .venv/bin/python spikes/unknown_model_probe.py
"""
import datetime, json, os, urllib.error, urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "evidence" / "platform" / "unknown-model.json"
IDS = {"retired_2026_08_31": "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1", "never_existed": "nvidia/mmd-no-such-model-2026"}


def probe(model):
    body = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    req = urllib.request.Request("https://api.tokenfactory.nebius.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]})
    at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return {"model": model, "utc": at, "status": r.status, "body": r.read()[:300].decode(errors="replace")}
    except urllib.error.HTTPError as e:
        return {"model": model, "utc": at, "status": e.code, "body": e.read()[:300].decode(errors="replace")}


def main():
    calls = {label: probe(model) for label, model in IDS.items()}
    statuses = sorted({c["status"] for c in calls.values() if c["status"] != 200})
    OUT.write_text(json.dumps({"plan": "evidence/platform/PLAN-unknown-model.md", "calls": calls, "statuses": statuses},
                              indent=1) + "\n")
    print(json.dumps({k: (v["status"], v["body"][:120]) for k, v in calls.items()}))


if __name__ == "__main__":
    main()
