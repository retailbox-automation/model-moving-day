"""Census steps for one target repo, run from the host (each sandbox step goes through Sandbox + Envelope).

    python3 tools/tfenv.py -- python -m model_moving_day.census prepare instructor
    python3 tools/tfenv.py -- python -m model_moving_day.census control instructor
    python -m model_moving_day.census coverage instructor          # no API calls; rebuilds coverage.json

Outputs go to evidence/census/<target>/ (public: no secrets, only counts, ids and short failure messages).
"""
import argparse, json, os, time
from pathlib import Path

from model_moving_day import find, runner, sandbox as sb, tf_client as tf

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "census" / "targets.json"
EVIDENCE = ROOT / "evidence" / "census"
CLONES = Path(os.environ.get("MMD_CLONES", "~/nebius-c1-targets")).expanduser()
SUPER = "nvidia/nemotron-3-super-120b-a12b"
FAILED = {"failed", "error"}


def load_target(name):
    d = json.loads(TARGETS.read_text())[name]
    return runner.Target(name, d["github"], d["commit"], d["install"], d["pytest_args"], d.get("env", {}),
                         d.get("exclusions", {})), d


def out_dir(name):
    p = EVIDENCE / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def ast_sites(name):
    res = find.scan(CLONES / name)
    return res


def outcomes(summary, run=0):
    """{test node id: outcome} for one of the K runs of a job summary."""
    if not summary or not summary.get("runs"):
        return {}
    return {t: v["outcome"] for t, v in summary["runs"][run].items()}


def controls_table(forward, broken, baseline=None):
    """Per traced call site: which tests reach it and whether their outcome flips between a good answer and a
    deliberately broken one. The good side is the cassette baseline when there is one, else the forward run."""
    reach = (forward.get("calls") or {}).get("tests") or {}
    good_name = "baseline" if baseline else "forward"
    good, bad, fwd = outcomes(baseline or forward), outcomes(broken), outcomes(forward)
    sites = {}
    for test, ts in reach.items():
        for s in ts:
            sites.setdefault(s, set()).add(test)
    rows = []
    for s, tests in sorted(sites.items()):
        flips, details = [], []
        for t in sorted(tests):
            d = {"test": t, good_name: good.get(t), "broken": bad.get(t)}
            if baseline:
                d["forward"] = fwd.get(t)
            details.append(d)
            if good.get(t) == "passed" and bad.get(t) in FAILED:
                flips.append(t)
        isolated = all(len(reach.get(t, [])) == 1 for t in flips)
        rows.append({"site": s, "tests": len(tests), "flip_tests": flips, "covered": bool(flips),
                     "isolated": isolated, "good_side": good_name, "details": details})
    return rows


def _save(name, label, r, summary):
    rec = {"label": label, "ts": round(time.time(), 3), "exit_code": r.exit_code, "elapsed_s": r.elapsed_s,
           "wall_s": r.wall_s, "sandbox_cost": r.cost, "summary": summary,
           "stdout_tail": r.stdout[-1500:] if summary is None else "", "stderr_tail": r.stderr[-1500:]}
    (out_dir(name) / f"{label}.json").write_text(json.dumps(rec, indent=1))
    return rec


def cmd_prepare(name):
    t, _ = load_target(name)
    box = sb.Sandbox()
    r, info = runner.prepare(box, t)
    rec = {"target": name, "commit": t.commit, "image_uuid": r.image_uuid, "tag": f"mmd-{t.name}:{t.commit[:12]}",
           "exit_code": r.exit_code, "elapsed_s": r.elapsed_s, "wall_s": r.wall_s, "sandbox_cost": r.cost, "info": info,
           "stdout_tail": r.stdout[-1500:], "stderr_tail": r.stderr[-2500:]}
    (out_dir(name) / "image.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps({k: v for k, v in rec.items() if not k.endswith("_tail")}))


def _routes(name, default):
    return {"default": default, "sites": {}, "spans": find.site_spans(ast_sites(name).sites)}


def cmd_control(name, which, cap):
    t, d = load_target(name)
    image = json.loads((out_dir(name) / "image.json").read_text())["image_uuid"]
    box, led = sb.Sandbox(), tf.Ledger()
    plan = {"baseline": ({"mode": "broken"}, [], False),  # proxy unused: the SDK keeps its own endpoint
            "forward": ({"mode": "forward", "model": SUPER, "reasoning": "keep"}, d.get("live_args", []), True),
            "broken": ({"mode": "broken"}, d.get("live_args", []), True)}
    for label in which:
        route, extra, proxy_env = plan[label]
        r, summary = runner.run_job(box, image, t, _routes(name, route), k=1, cap_usd=cap, ledger=led,
                                    note=f"control {label}", extra_args=extra, proxy_env=proxy_env)
        rec = _save(name, f"control-{label}", r, summary)
        s = rec["summary"] or {}
        oc = outcomes(s)
        print(json.dumps({"label": label, "exit": r.exit_code, "sandbox_cost": r.cost, "elapsed_s": r.elapsed_s,
                          "wall_s": r.wall_s, "spent": s.get("envelope_spent"), "error": s.get("error"),
                          "tests": len(oc), "passed": sum(v == "passed" for v in oc.values()),
                          "failed": sum(v in FAILED for v in oc.values()), "skipped": sum(v == "skipped" for v in oc.values()),
                          "calls": sum(x["calls"] for x in (s.get("calls") or {}).get("sites", {}).values())}))
    cmd_coverage(name)


def cmd_coverage(name):
    load = lambda label: (json.loads(p.read_text()).get("summary") if (p := out_dir(name) / f"control-{label}.json").exists() else None)
    fwd, brk, base = load("forward"), load("broken"), load("baseline")
    scan = ast_sites(name)
    rows = controls_table(fwd or {}, brk or {}, base) if fwd and brk else []
    reached = {r["site"] for r in rows}
    out = {"target": name, "ast_sites": [{"id": s.id, "func": s.func, "sdk": s.sdk, "api": s.api, "kind": s.kind,
                                          "model_expr": s.model_expr, "model_literal": s.model_literal} for s in scan.sites],
           "ast_parse_errors": scan.errors, "traced_sites": rows,
           "ast_sites_not_reached": sorted({s.id for s in scan.sites} - reached),
           "covered_sites": [r["site"] for r in rows if r["covered"]]}
    (out_dir(name) / "coverage.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"target": name, "ast_sites": len(scan.sites), "traced_sites": len(rows),
                      "covered": out["covered_sites"]}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "control", "coverage"])
    ap.add_argument("target")
    ap.add_argument("--which", default="forward,broken")
    ap.add_argument("--cap", type=float, default=0.50)
    a = ap.parse_args()
    if a.cmd == "prepare":
        cmd_prepare(a.target)
    elif a.cmd == "control":
        cmd_control(a.target, a.which.split(","), a.cap)
    else:
        cmd_coverage(a.target)


if __name__ == "__main__":
    main()
