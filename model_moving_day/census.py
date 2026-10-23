"""Census steps for one target repo, run from the host (each sandbox step goes through Sandbox + Envelope).

    python3 tools/tfenv.py -- python -m model_moving_day.census prepare instructor
    python3 tools/tfenv.py -- python -m model_moving_day.census control instructor
    python -m model_moving_day.census coverage instructor          # no API calls; rebuilds coverage.json

Outputs go to evidence/census/<target>/ (public: no secrets, only counts, ids and short failure messages).
"""
import argparse, json, os, time
from pathlib import Path

from model_moving_day import find, ladder, runner, sandbox as sb, tf_client as tf

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
    ap.add_argument("cmd", choices=["prepare", "control", "coverage", "ladder"])
    ap.add_argument("target")
    ap.add_argument("--which", default="forward,broken")
    ap.add_argument("--cap", type=float, default=0.50)
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--margin", type=float, default=0.2)
    ap.add_argument("--rungs", default="")
    a = ap.parse_args()
    if a.cmd == "prepare":
        cmd_prepare(a.target)
    elif a.cmd == "control":
        cmd_control(a.target, a.which.split(","), a.cap)
    elif a.cmd == "ladder":
        cmd_ladder(a.target, a.k, a.margin, a.cap, [r for r in a.rungs.split(",") if r])
    else:
        cmd_coverage(a.target)



# --- ladder ---------------------------------------------------------------------------------------------------
NANO, LIGHT, ULTRA = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3_5-Lightning", "nvidia/Nemotron-3-Ultra-550b-a55b"
RUNGS = [("Nano", NANO), ("Lightning", LIGHT), ("Super", SUPER), ("Ultra", ULTRA)]


def site_cells(summary, site, flip_tests, k):
    """One RunOutcome per run i: passed iff every flip test of the site passed in run i (missing -> infra)."""
    calls = ((summary or {}).get("calls") or {}).get("sites", {}).get(site, {})
    runs = (summary or {}).get("runs") or []
    outs = []
    for i in range(k):
        res = runs[i] if i < len(runs) else {}
        oc = [res.get(t, {}).get("outcome") for t in flip_tests]
        if any(o is None or o == "infra" for o in oc) or not oc:
            outs.append(ladder.RunOutcome(None, failure="test missing from the run (infrastructure)"))
            continue
        bad = [(t, res[t]["message"]) for t in flip_tests if res[t]["outcome"] != "passed"]
        outs.append(ladder.RunOutcome(not bad, failure=(f"{bad[0][0]}: {bad[0][1][:300]}" if bad else ""),
                                      tests={t: res[t]["outcome"] for t in flip_tests}))
    # tokens and $ are per site over all K runs (the proxy log does not split runs); attach to the first outcome
    if outs:
        o = outs[0]
        o.calls, o.prompt_tokens = calls.get("calls", 0), calls.get("prompt_tokens", 0)
        o.completion_tokens, o.reasoning_tokens, o.cost = calls.get("completion_tokens", 0), calls.get("reasoning_tokens", 0), calls.get("cost", 0.0)
    return outs, calls


def cell_record(cell, calls, model, k, baseline, margin):
    lo, hi = cell.interval()
    tin, tout = cell.tokens_per_call()
    price = tf.PRICES[model]
    return {"rung": cell.rung, "model": model, "params": cell.params, "k": k, "valid_runs": cell.n, "passed_runs": cell.k,
            "infra_errors": cell.infra, "rate": None if cell.n == 0 else round(cell.rate, 4),
            "wilson95": [round(lo, 4), round(hi, 4)],
            "fail_rate_upper_rule_of_three": round(ladder.rule_of_three(cell.n), 4) if cell.n and cell.k == cell.n else None,
            "passes": ladder.cell_passes(cell, baseline, margin, k), "calls": calls.get("calls", 0),
            "tokens_per_call": [None if tin != tin else round(tin, 1), None if tout != tout else round(tout, 1)],
            "reasoning_tokens": calls.get("reasoning_tokens", 0),
            "usd_per_1000_calls": None if tin != tin else round(ladder.usd_per_1000_calls(tin, tout, price), 4),
            "model_cost": round(cell.cost, 6), "diagnoses": calls.get("diagnoses", {}), "finish": calls.get("finish", {}),
            "changes": calls.get("changes", {}), "first_failure": next((r.failure for r in cell.runs if r.passed is False), ""),
            "per_run": [{"passed": r.passed, "tests": r.tests} for r in cell.runs]}


def cmd_ladder(name, k, margin, cap, rungs_wanted=None, use_brain=True):
    from model_moving_day import agent
    t, d = load_target(name)
    cov = json.loads((out_dir(name) / "coverage.json").read_text())
    image = json.loads((out_dir(name) / "image.json").read_text())["image_uuid"]
    base_ctl = out_dir(name) / "control-baseline.json"
    flips = {r["site"]: r["flip_tests"] for r in cov["traced_sites"] if r["covered"]}
    baseline_note = "measured: cassette replay (K=1)" if base_ctl.exists() else "assumed 1.0: upstream CI runs these tests live"
    box, led = sb.Sandbox(), tf.Ledger()
    brain = agent.Brain(tf.Client("c1"), agent.pick_brain(CLONES / name), out_dir(name) / "agent-steps.jsonl") if use_brain else None
    spans = find.site_spans(ast_sites(name).sites)
    state = {s: {"cells": [], "chosen": None, "fix": None, "reason": "", "flip_tests": f} for s, f in flips.items()}
    jobs = []

    def run(model, sites, site_params, note):
        tests = sorted({x for s in sites for x in flips[s]})
        routes = {"default": {"mode": "forward", "model": model, "reasoning": "keep"},
                  "sites": {s: {"mode": "forward", "model": model, **p} for s, p in site_params.items()}, "spans": spans}
        tt = runner.Target(t.name, t.github, t.commit, t.install, tests + d["node_args"], t.env)
        r, summary = runner.run_job(box, image, tt, routes, k=k, cap_usd=cap, ledger=led, note=note)
        jobs.append({"note": note, "model": model, "tests": len(tests), "sandbox_cost": r.cost, "cpu_s": r.elapsed_s,
                     "wall_s": r.wall_s, "model_spend": (summary or {}).get("envelope_spent"),
                     "error": (summary or {}).get("error"), "durations_s": (summary or {}).get("durations_s")})
        print(json.dumps(jobs[-1]), flush=True)
        return summary

    for rung, model in RUNGS:
        if rungs_wanted and rung not in rungs_wanted:
            continue
        todo = [s for s in state if state[s]["chosen"] is None]
        if not todo:
            break
        summary = run(model, todo, {}, f"ladder {rung}")
        needs_fix = {}
        for s in todo:
            outs, calls = site_cells(summary, s, flips[s], k)
            cell = ladder.Cell(s, rung, outs)
            rec = cell_record(cell, calls, model, k, 1.0, margin)
            state[s]["cells"].append(rec)
            if rec["passes"]:
                state[s].update(chosen=rung, model=model, reason=f"{rung} passed {cell.k}/{cell.n}")
            elif brain and calls.get("diagnoses"):
                fix = brain.diagnose({"site": s, "rung": rung, "model": model, "passed_runs": cell.k, "valid_runs": cell.n,
                                      "first_failure": rec["first_failure"], "diagnoses": calls.get("diagnoses"),
                                      "finish": calls.get("finish"), "max_tokens": calls.get("max_tokens"),
                                      "calls": calls.get("calls"), "reasoning_tokens": calls.get("reasoning_tokens"),
                                      "completion_tokens": calls.get("completion_tokens")})
                if fix:
                    needs_fix[s] = fix
        if needs_fix:
            summary = run(model, list(needs_fix), {s: f.params for s, f in needs_fix.items()}, f"ladder {rung} + fix")
            for s, fix in needs_fix.items():
                outs, calls = site_cells(summary, s, flips[s], k)
                cell = ladder.Cell(s, rung, outs, dict(fix.params))
                rec = cell_record(cell, calls, model, k, 1.0, margin)
                rec["fix"] = {"kind": fix.kind, "why": fix.why, "by": fix.by}
                state[s]["cells"].append(rec)
                if rec["passes"]:
                    state[s].update(chosen=rung, model=model, fix=rec["fix"], reason=f"{rung} passed {cell.k}/{cell.n} after {fix.kind}")
    for s in state:
        if state[s]["chosen"] is None:
            state[s]["reason"] = "no model passed, the old model stays"
    out = {"target": name, "commit": t.commit, "k": k, "margin": margin, "baseline": baseline_note, "rungs": [r for r, _ in RUNGS],
           "sites": state, "jobs": jobs, "brain": brain.model if brain else None}
    (out_dir(name) / "ladder.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({s: (v["chosen"], v["reason"]) for s, v in state.items()}))
    return out


if __name__ == "__main__":
    main()
