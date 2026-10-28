"""Build demo/data/census.json from the evidence: the same dict that renders CENSUS.md (census/report.py data()), plus
the patch texts (PR.md, patch.diff) of each migration branch. Deterministic: building twice gives the same bytes.

    .venv/bin/python -m demo.build          # writes demo/data/census.json
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "data" / "census.json"
ABOUT = {
    "what": "Model Moving Day: which NVIDIA Nemotron model on Nebius Token Factory each LLM call site needs, decided by "
            "the repository's own tests. This demo replays the recorded census; it makes no model calls.",
    "source": "Every number comes from the evidence files in this repository, through census/report.py (the same "
              "function writes CENSUS.md). To run the census yourself with your own Token Factory key, see README.md.",
    "honesty": "Test traffic, not production traffic. The coverage gate written in our internal notes before the runs "
               "(3 repositories, 5 independent decisions; not in the pushed manifest) was not met. Limits and what is not claimed: README.md and CENSUS.md.",
}


def _report():
    spec = importlib.util.spec_from_file_location("census_report", ROOT / "census" / "report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rerun(d=ROOT / "evidence" / "rerun-2026-10-29"):
    """The recorded re-run of llm :1494 (not part of the census): its cells, calls with response ids, speed, spend."""
    ladders = sorted(d.glob("ladder-*.json"))
    if not ladders:
        return None
    docs = [json.loads(p.read_text()) for p in ladders]
    dec = next(x for x in docs if x["kind"] == "decision")
    conf = next((x for x in docs if x["kind"] == "confirm"), None)
    calls = [json.loads(x) for x in (d / "calls.jsonl").read_text().splitlines() if x.strip()]
    site, st = next(iter(dec["sites"].items()))
    cells = [{k: c.get(k) for k in ("rung", "model", "params", "passed_runs", "valid_runs", "status",
                                    "usd_per_1000_calls", "seconds_per_call", "first_failure")} for c in st["cells"]]
    brain = [json.loads(x) for f in ("agent-steps-ladder.jsonl", "agent-steps-patch.jsonl")
             for x in (d / f).read_text().splitlines() if x.strip()]
    return {"site": site, "run_id": dec["run_id"], "manifest_sha256": dec["manifest"]["sha256"],
            "manifest_pushed_at": dec["manifest"].get("pushed_at"), "chosen": st.get("chosen"), "reason": st.get("reason"),
            "cells": cells, "confirm": None if conf is None else
            {"run_id": conf["run_id"], "cells": [{k: c.get(k) for k in ("passed_runs", "valid_runs", "status")}
                                                 for v in conf["sites"].values() for c in v["cells"]]},
            "brain_steps": len(brain), "brain_models": sorted({b.get("model") for b in brain}),
            "brain_seconds": round(sum(b.get("seconds") or 0 for b in brain), 1),
            "calls": len(calls), "calls_with_id": sum(1 for c in calls if c.get("upstream_id")),
            "model_cost_usd": round(sum(c.get("cost") or 0 for c in calls), 4),
            "first_ids": [c["upstream_id"] for c in calls[:3]], "cast": "evidence/rerun-2026-10-29/rerun.cast",
            "result": "evidence/rerun-2026-10-29/RESULT.md"}


def scan(root=ROOT / "evidence" / "scan"):
    """The latest static scan (evidence/scan/<date>/): headline T/S/U of K, error counts, the rule commit."""
    runs = sorted(p for p in root.glob("*/results.json"))
    if not runs:
        return None
    d = runs[-1].parent
    results = json.loads((d / "results.json").read_text())
    repos = json.loads((d / "repos.json").read_text())
    k = len(results)
    hit = lambda key: sum(1 for r in results if r[key])
    return {"date": d.name, "K": k, "T": hit("test_ids"), "S": hit("source_ids"),
            "U": sum(1 for r in results if r["test_ids"] or r["source_ids"]),
            "candidates": len(repos), "clone_errors": sum(1 for r in repos if r.get("reason") == "clone failed"),
            "scan_errors": sum(1 for r in repos if r.get("reason") == "scan failed"),
            "parse_errors": sum(len(r["errors"]) for r in results), "dir": f"evidence/scan/{d.name}"}


def build():
    census = _report().data()
    files = {}
    for p in census["patches"]:
        d = ROOT / "evidence" / "census" / p["target"] / "patch"
        files[p["target"]] = {"pr_md": (d / "PR.md").read_text(), "diff": (d / "patch.diff").read_text()}
    return {"schema": 1, "about": ABOUT, "census": census, "patch_files": files, "rerun": rerun(), "scan": scan()}


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(dumps(build()))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
