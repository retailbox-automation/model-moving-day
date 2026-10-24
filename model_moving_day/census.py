"""Census steps for one target repo, run from the host (each sandbox step goes through Sandbox + Envelope).

    python3 tools/tfenv.py -- python -m model_moving_day.census prepare instructor
    python3 tools/tfenv.py -- python -m model_moving_day.census control instructor [--which forward,broken,wrong]
    python -m model_moving_day.census coverage instructor          # no API calls; rebuilds coverage.json
    python -m model_moving_day.census manifest --targets instructor,llm --reason "..."   # writes census/MANIFEST.json
    python3 tools/tfenv.py -- python -m model_moving_day.census ladder instructor [--smoke ID] [--pushed-at ISO]
    python3 tools/tfenv.py -- python -m model_moving_day.census patch instructor

The ladder takes K, margin, rungs, flip tests and frozen hashes from census/MANIFEST.json and refuses to run when the
manifest does not match the code, the targets file, the evidence, the clone, or is not pushed.
Outputs go to evidence/census/<target>/ (public: no secrets, only counts, ids and short failure messages).
"""
import argparse, datetime, json, os, subprocess, time, uuid
from pathlib import Path

from model_moving_day import find, ladder, runner, sandbox as sb, tf_client as tf
from model_moving_day import manifest as mf
from model_moving_day.ladder import NANO, LIGHT, SUPER, ULTRA, RUNGS

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ROOT / "census" / "targets.json"
EVIDENCE = ROOT / "evidence" / "census"
CLONES = Path(os.environ.get("MMD_CLONES", "~/nebius-c1-targets")).expanduser()
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


def controls_table(forward, broken, baseline=None, wrong=None):
    """Per traced call site: which tests reach it and whether their outcome flips between a good answer and a
    deliberately broken one. The good side is the cassette baseline when there is one, else the forward run.
    With a `wrong` control (well-formed wrong answers) a test counts only when it flips on both broken answers; a
    test with no outcome in the wrong control (run lost, killed) keeps its empty-answer flip and is listed under
    `wrong_control_missing`, so a failed control never shrinks coverage silently.
    `cassette_only`: the site's flips rest on a recorded good answer; live forward and live broken do not differ."""
    reach = (forward.get("calls") or {}).get("tests") or {}
    good_name = "baseline" if baseline else "forward"
    good, bad, fwd, wr = outcomes(baseline or forward), outcomes(broken), outcomes(forward), outcomes(wrong)
    sites = {}
    for test, ts in reach.items():
        for s in ts:
            sites.setdefault(s, set()).add(test)
    rows = []
    for s, tests in sorted(sites.items()):
        flips, flips_empty, live_flips, missing, details = [], [], [], [], []
        for t in sorted(tests):
            d = {"test": t, good_name: good.get(t), "broken": bad.get(t)}
            if baseline:
                d["forward"] = fwd.get(t)
            if wrong is not None:
                d["wrong"] = wr.get(t)
            details.append(d)
            empty = good.get(t) == "passed" and bad.get(t) in FAILED
            if empty:
                flips_empty.append(t)
            if empty and wrong is not None and wr.get(t) in (None, "infra"):
                missing.append(t)
            if empty and (wrong is None or wr.get(t) in FAILED or t in missing):
                flips.append(t)
            if fwd.get(t) == "passed" and bad.get(t) in FAILED:
                live_flips.append(t)
        isolated = all(len(reach.get(t, [])) == 1 for t in flips)
        row = {"site": s, "tests": len(tests), "flip_tests": flips, "covered": bool(flips), "isolated": isolated,
               "good_side": good_name, "cassette_only": bool(baseline) and bool(flips) and not live_flips,
               "wrong_control": wrong is not None, "details": details}
        if wrong is not None:
            row["flip_tests_empty_only"] = [t for t in flips_empty if t not in flips]
            row["wrong_control_missing"] = missing
        rows.append(row)
    return rows


def untraced_calls(summary):
    """Calls the tracer did not tag, outside the broken controls (the proxy then routes them by default)."""
    calls = (summary or {}).get("calls") or {}
    if "untraced" in calls:  # written by the phase-B job
        return sum(v for m, v in calls["untraced"].items() if m not in ("broken", "wrong"))
    return ((calls.get("sites") or {}).get("unknown") or {}).get("calls", 0)  # older summaries: the 'unknown' site


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
            "broken": ({"mode": "broken"}, d.get("live_args", []), True),
            "wrong": ({"mode": "wrong"}, d.get("live_args", []), True)}
    for label in which:
        route, extra, proxy_env = plan[label]
        routes = dict(_routes(name, route), **({"embedding_model": d["embedding_model"]} if d.get("embedding_model") else {}))
        r, summary = runner.run_job(box, image, t, routes, k=1, cap_usd=cap, ledger=led,
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
    fwd, brk, base, wrong = load("forward"), load("broken"), load("baseline"), load("wrong")
    _, d = load_target(name)
    for label, summ in (("forward", fwd), ("wrong", wrong)):
        if untraced_calls(summ):
            raise RuntimeError(f"control-{label}: {untraced_calls(summ)} calls were not tagged by the tracer; "
                               "fix the tracer for this target before trusting any coverage")
    scan = ast_sites(name)
    rows = controls_table(fwd or {}, brk or {}, base, wrong) if fwd and brk else []
    excluded = d.get("site_exclusions", {})
    for r in rows:
        if r["site"] in excluded:  # decided before the ladder, with the reason, in targets.json and the manifest
            r.update(excluded=excluded[r["site"]], covered=False)
    reached = {r["site"] for r in rows}
    out = {"target": name, "ast_sites": [{"id": s.id, "func": s.func, "sdk": s.sdk, "api": s.api, "kind": s.kind,
                                          "model_expr": s.model_expr, "model_literal": s.model_literal} for s in scan.sites],
           "ast_parse_errors": scan.errors, "traced_sites": rows,
           "ast_sites_not_reached": sorted({s.id for s in scan.sites} - reached),
           "excluded_sites": excluded,
           "covered_sites": [r["site"] for r in rows if r["covered"]]}
    (out_dir(name) / "coverage.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"target": name, "ast_sites": len(scan.sites), "traced_sites": len(rows),
                      "covered": out["covered_sites"]}))




# --- ladder ---------------------------------------------------------------------------------------------------
class IntegrityError(RuntimeError):
    """The run cannot be trusted (frozen checks changed, the spend envelope broke, calls went untraced): stop."""


def _utc(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time(), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def site_cells(summary, site, flip_tests, k):
    """One RunOutcome per run i: passed iff every flip test of the site passed in run i. A run is infrastructure
    (passed=None) when the job left no summary or failed, the run timed out, a flip test is missing from its junit,
    or a flip test failed while the run logged an infra status (ladder.is_infra_status) at a site the failing tests
    reach. Calls logged without a run index count against every failing run."""
    s = summary or {}
    if not s:
        return [ladder.RunOutcome(None, failure="no job summary (sandbox run lost)") for _ in range(k)]
    if s.get("error"):
        return [ladder.RunOutcome(None, failure=f"job error: {s['error'][:300]}") for _ in range(k)]
    calls = s.get("calls") or {}
    sites, reach = calls.get("sites") or {}, calls.get("tests") or {}
    runs, codes = s.get("runs") or [], s.get("exit_codes") or []
    outs = []
    for i in range(k):
        res = runs[i] if i < len(runs) else {}
        if i < len(codes) and codes[i] == "timeout":
            outs.append(ladder.RunOutcome(None, failure=f"run {i} timed out"))
            continue
        oc = [res.get(t, {}).get("outcome") for t in flip_tests]
        if not oc or any(o is None or o == "infra" for o in oc):
            outs.append(ladder.RunOutcome(None, failure=f"run {i}: a flip test is missing from the run (infrastructure)"))
            continue
        bad = [(t, res[t].get("message", "")) for t in flip_tests if res[t]["outcome"] != "passed"]
        tests = {t: res[t]["outcome"] for t in flip_tests}
        if not bad:
            outs.append(ladder.RunOutcome(True, tests=tests))
            continue
        touched = {site} | {x for t, _ in bad for x in reach.get(t, [])}
        infra = {}
        for x in touched:
            by_run = (sites.get(x) or {}).get("errors_by_run") or {}
            for code, n in list((by_run.get(str(i)) or {}).items()) + list((by_run.get("?") or {}).items()):
                if ladder.is_infra_status(code):
                    infra[code] = infra.get(code, 0) + n
        if infra:
            outs.append(ladder.RunOutcome(None, failure=f"run {i}: failed with infra statuses {infra}; "
                                                        f"{bad[0][0]}: {bad[0][1][:200]}", tests=tests))
        else:
            outs.append(ladder.RunOutcome(False, failure=f"{bad[0][0]}: {bad[0][1][:300]}", tests=tests))
    return outs


SUMS = ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens", "cost", "seconds", "repaired_empty_args")
COUNTS = ("models", "diagnoses", "errors", "finish", "changes", "max_tokens")


def merge_stats(parts):
    """Per-site call statistics of one cell over all its jobs (attempts)."""
    out = {k: 0 for k in SUMS}
    out.update({k: {} for k in COUNTS})
    for p in parts:
        for k in SUMS:
            out[k] = round(out[k] + (p.get(k) or 0), 8)
        for k in COUNTS:
            for key, n in (p.get(k) or {}).items():
                out[k][key] = out[k].get(key, 0) + n
    return out


def cell_record(cell, stats, model, k, baseline, margin, flip_tests):
    lo, hi = cell.interval()
    calls = stats.get("calls", 0)
    tin = stats.get("prompt_tokens", 0) / calls if calls else None
    tout = stats.get("completion_tokens", 0) / calls if calls else None
    passes = ladder.cell_passes(cell, baseline, margin, k)
    status = "pass" if passes else ("fail" if cell.n >= k or ladder.decided_red(cell, baseline, margin, k) else "infra")
    per_test = {t: [sum(1 for r in cell.valid if r.tests.get(t) == "passed"), cell.n] for t in flip_tests}
    runs_seen = len(cell.runs)
    return {"rung": cell.rung, "model": model, "params": cell.params, "k": k, "valid_runs": cell.n, "passed_runs": cell.k,
            "infra_runs": cell.infra, "status": status, "passes": passes,
            "rate": None if cell.n == 0 else round(cell.rate, 4), "wilson95": [round(lo, 4), round(hi, 4)],
            "fail_rate_upper_rule_of_three": round(ladder.rule_of_three(cell.n), 4) if cell.n and cell.k == cell.n else None,
            "calls": calls, "calls_per_test_run": round(calls / (len(flip_tests) * runs_seen), 3) if runs_seen and flip_tests else None,
            "tokens_per_call": [None if tin is None else round(tin, 1), None if tout is None else round(tout, 1)],
            "reasoning_tokens": stats.get("reasoning_tokens", 0),
            "usd_per_1000_calls": None if tin is None else round(ladder.usd_per_1000_calls(tin, tout, tf.PRICES[model]), 4),
            "model_cost": round(stats.get("cost", 0.0), 6),
            "usd_per_passed_run": round(stats.get("cost", 0.0) / cell.k, 6) if cell.k else None,
            "diagnoses": stats.get("diagnoses", {}), "finish": stats.get("finish", {}), "changes": stats.get("changes", {}),
            "errors": stats.get("errors", {}), "repaired_empty_args": stats.get("repaired_empty_args", 0),
            "first_failure": next((r.failure for r in cell.runs if r.passed is False), ""),
            "infra_failures": [r.failure for r in cell.runs if r.passed is None][:5],
            "per_test_passed": per_test, "per_run": [{"passed": r.passed, "tests": r.tests} for r in cell.runs]}


class Ladder:
    """One ladder run over one target: climb, at most one fix per red cell, infra re-runs, measured fallbacks.
    After every job and every cell the run is appended to ladder-log.jsonl and checkpointed to its own file."""

    def __init__(self, name, entry, rules, k, rungs, kind, out, run_job, brain=None, box=None, ledger=None,
                 manifest=None, smoke_id=None, cap=mf.CAP_PER_JOB_USD, spans=None):
        self.name, self.entry, self.rules, self.k, self.rungs, self.kind = name, entry, rules, k, rungs, kind
        self.margin, self.baseline = rules["margin"], rules["baseline_pass_rate"]
        self.out, self.run_job, self.brain, self.box, self.ledger, self.cap = Path(out), run_job, brain, box, ledger, cap
        self.flips, self.spans = entry["flip_tests"], spans or {}
        self.run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:6]
        self.path, self.log_path = self.out / f"ladder-{self.run_id}.json", self.out / "ladder-log.jsonl"
        self.state = {s: {"status": "pending", "chosen": None, "model": None, "fix": None, "fallback": None, "reason": "",
                          "flip_tests": f, "flags": (entry.get("site_flags") or {}).get(s, {}), "cells": []}
                      for s, f in self.flips.items()}
        self.jobs = []
        self.doc = {"target": name, "run_id": self.run_id, "kind": kind, "smoke": smoke_id, "status": "running",
                    "started_utc": _utc(), "commit": entry["commit"], "manifest": manifest or {}, "k": k,
                    "margin": self.margin, "baseline": self.baseline,
                    "baseline_note": "pass rate assumed 1.0 (strictest), not measured by us. " + entry.get("baseline_evidence", ""),
                    "rungs": [r for r, _ in rungs], "excluded_sites": entry.get("excluded_sites", {}),
                    "brain": getattr(brain, "model", None), "frozen_sha256": entry["frozen"]["sha256"],
                    "sandbox_max_runs": getattr(box, "max_runs", None), "sites": self.state, "jobs": self.jobs}
        self.out.mkdir(parents=True, exist_ok=True)

    def event(self, event, **data):
        with open(self.log_path, "a") as f:
            f.write(json.dumps({"utc": _utc(), "run_id": self.run_id, "target": self.name, "event": event, **data}) + "\n")

    def checkpoint(self):
        self.path.write_text(json.dumps(self.doc, indent=1))

    def job(self, model, sites, params, k, note, routes=None, env=None, files=None):
        tests = sorted({t for s in sites for t in self.flips[s]})
        routes = routes or {"default": {"mode": "forward", "model": model, "reasoning": "keep"},
                            "sites": {s: {"mode": "forward", "model": model, **params.get(s, {})} for s in sites if params.get(s)},
                            "spans": self.spans}
        e = self.entry
        t = runner.Target(self.name, e["github"], e["commit"], e["install"], tests + e["node_args"], e.get("env", {}))
        r, summary, error = None, None, None
        try:
            r, summary = self.run_job(self.box, e["image"]["uuid"], t, routes, k=k, cap_usd=self.cap, ledger=self.ledger,
                                      frozen_hashes=e["frozen"]["hashes"], frozen_extra=e["frozen"]["extra_paths"],
                                      require_frozen=True, note=f"{self.kind} {note}", env=env, extra_files=files)
        except (sb.SandboxRunLimit, tf.BudgetExceeded):
            raise
        except Exception as ex:  # a lost sandbox run is an infrastructure error for every run of this job
            error = f"{type(ex).__name__}: {ex}"[:500]
        s = summary or {}
        rec = {"utc": _utc(), "note": note, "model": model, "sites": sites, "k": k, "tests": len(tests),
               "sandbox_cost_diagnostic": getattr(r, "cost", None), "cpu_s": getattr(r, "elapsed_s", None),
               "wall_s": getattr(r, "wall_s", None), "exit_code": getattr(r, "exit_code", None),
               "model_spend": s.get("envelope_spent"), "error": error or s.get("error"),
               "envelope_error": s.get("envelope_error"), "exit_codes": s.get("exit_codes"),
               "durations_s": s.get("durations_s"), "freeze_before": s.get("freeze_before"),
               "freeze_after": s.get("freeze_after"), "isolation": s.get("isolation"), "test_uid": s.get("test_uid"),
               "untraced": untraced_calls(s)}
        self.jobs.append(rec)
        self.event("job", **rec)
        self.checkpoint()
        print(json.dumps({k_: rec[k_] for k_ in ("note", "model", "k", "tests", "model_spend", "error", "durations_s")}), flush=True)
        if s.get("freeze_before") or [x for x in s.get("freeze_after") or [] if not x.startswith("added ")]:
            raise IntegrityError(f"frozen checks differ: before {s.get('freeze_before')}, after {s.get('freeze_after')}")
        if s.get("envelope_error"):
            raise IntegrityError("spend envelope broke: " + s["envelope_error"])
        if rec["untraced"]:
            raise IntegrityError(f"{rec['untraced']} calls were not tagged by the tracer")
        return summary

    def measure(self, rung, model, sites, params, note, **job_kw):
        """Run K runs per site, re-running only missing valid runs (at most MAX_INFRA_RETRIES times)."""
        outs, parts = {s: [] for s in sites}, {s: [] for s in sites}
        pending, attempt = list(sites), 0
        while pending and attempt <= ladder.MAX_INFRA_RETRIES:
            valid = {s: sum(1 for o in outs[s] if o.passed is not None) for s in pending}
            need = max(self.k - v for v in valid.values())
            summary = self.job(model, pending, params, need, note + (f" (infra re-run {attempt})" if attempt else ""), **job_kw)
            for s in pending:
                new = site_cells(summary, s, self.flips[s], need)
                room = self.k - valid[s]
                for o in new:  # keep every infra outcome, and valid ones only up to K
                    if o.passed is None:
                        outs[s].append(o)
                    elif room > 0:
                        outs[s].append(o)
                        room -= 1
                parts[s].append((((summary or {}).get("calls") or {}).get("sites") or {}).get(s, {}))
            attempt += 1
            pending = [s for s in pending
                       if sum(1 for o in outs[s] if o.passed is not None) < self.k
                       and not ladder.decided_red(ladder.Cell(s, rung, outs[s]), self.baseline, self.margin, self.k)]
        return {s: (outs[s], merge_stats(parts[s])) for s in sites}

    def record(self, site, rung, model, outs, stats, params=None, role="ladder", fix=None):
        cell = ladder.Cell(site, rung, outs, dict(params or {}))
        rec = cell_record(cell, stats, model, self.k, self.baseline, self.margin, self.flips[site])
        rec["role"] = role
        if fix is not None:
            expected = set(fix.expect)
            applied = sorted(expected & set(rec["changes"]))
            rec["fix"] = {"kind": fix.kind, "why": fix.why, "by": fix.by, "expected_change": sorted(expected),
                          "applied": bool(applied), "applied_calls": sum(rec["changes"].get(c, 0) for c in applied)}
            if not applied and rec["passes"]:
                rec.update(passes=False, status="fix not applied")
        self.state[site]["cells"].append(rec)
        self.event("cell", site=site, rung=rung, role=role, status=rec["status"], passed=rec["passed_runs"],
                   valid=rec["valid_runs"], infra=rec["infra_runs"], fix=rec.get("fix"))
        self.checkpoint()
        return rec

    def settle(self, site, **kw):
        self.state[site].update(**kw)
        self.event("site", site=site, **{k: v for k, v in kw.items() if k != "fix"})
        self.checkpoint()

    def diagnose(self, site, rung, model, rec, stats):
        try:
            return self.brain.diagnose({
                "site": site, "rung": rung, "model": model, "passed_runs": rec["passed_runs"], "valid_runs": rec["valid_runs"],
                "first_failure": rec["first_failure"], "diagnoses": stats.get("diagnoses"), "finish": stats.get("finish"),
                "max_tokens": stats.get("max_tokens"), "calls": stats.get("calls"),
                "reasoning_tokens": stats.get("reasoning_tokens"), "completion_tokens": stats.get("completion_tokens")})
        except tf.TFError as e:  # the brain could not be asked: the site must not climb because of that
            rec["brain_error"] = str(e)[:300]
            self.settle(site, status="infra", reason=f"diagnosis call failed at {rung}: {str(e)[:200]}")
            return None

    def run(self):
        self.event("start", kind=self.kind, k=self.k, rungs=self.doc["rungs"], manifest=self.doc["manifest"])
        self.checkpoint()
        try:
            for i, (rung, model) in enumerate(self.rungs):
                todo = [s for s, v in self.state.items() if v["status"] == "pending"]
                if not todo:
                    break
                needs_fix = {}
                for s, (outs, stats) in self.measure(rung, model, todo, {}, rung).items():
                    rec = self.record(s, rung, model, outs, stats)
                    if rec["passes"]:
                        self.settle(s, status="chosen", chosen=rung, model=model, reason=f"{rung} passed {rec['passed_runs']}/{rec['valid_runs']}")
                    elif rec["status"] == "infra":
                        self.settle(s, status="infra", reason=f"infrastructure errors persisted at {rung} after "
                                                              f"{ladder.MAX_INFRA_RETRIES} re-runs; not climbed")
                    elif self.brain and stats.get("diagnoses"):
                        fix = self.diagnose(s, rung, model, rec, stats)
                        if fix:
                            needs_fix[s] = fix
                if needs_fix:
                    res = self.measure(rung, model, list(needs_fix), {s: f.params for s, f in needs_fix.items()}, f"{rung} + fix")
                    for s, (outs, stats) in res.items():
                        rec = self.record(s, rung, model, outs, stats, needs_fix[s].params, fix=needs_fix[s])
                        if rec["passes"]:
                            self.settle(s, status="chosen", chosen=rung, model=model, fix=rec["fix"],
                                        reason=f"{rung} passed {rec['passed_runs']}/{rec['valid_runs']} after {needs_fix[s].kind}")
                        elif rec["status"] == "infra":
                            self.settle(s, status="infra", reason=f"infrastructure errors persisted at {rung} + fix; not climbed")
            for s, v in self.state.items():
                if v["status"] == "pending":
                    if self.kind == "smoke":
                        self.settle(s, status="not decided", reason=f"smoke run on {', '.join(self.doc['rungs'])} only")
                    else:
                        self.settle(s, status="red", reason="no model passed, the old model stays")
            if self.kind == "decision":
                self.fallbacks()
            self.doc["status"] = "complete"
        except BaseException as e:
            self.doc.update(status="aborted", error=f"{type(e).__name__}: {e}"[:800])
            for s, v in self.state.items():
                if v["status"] == "pending":
                    v.update(status="stopped", reason="the ladder stopped before this site was decided")
            raise
        finally:
            self.doc["ended_utc"] = _utc()
            self.event("end", status=self.doc["status"], error=self.doc.get("error"))
            self.checkpoint()
        return self.doc

    def fallbacks(self):
        """One measured cell on the next rung up for every chosen site; the helper may only fall back to a pass."""
        names = [r for r, _ in self.rungs]
        groups = {}
        for s, v in self.state.items():
            if v["status"] == "chosen" and names.index(v["chosen"]) + 1 < len(names):
                groups.setdefault(names.index(v["chosen"]) + 1, []).append(s)
        for idx, sites in sorted(groups.items()):
            rung, model = self.rungs[idx]
            for s, (outs, stats) in self.measure(rung, model, sites, {}, f"{rung} fallback").items():
                rec = self.record(s, rung, model, outs, stats, role="fallback")
                self.state[s]["fallback"] = {"rung": rung, "model": model, "passes": rec["passes"], "status": rec["status"]}
                self.event("fallback", site=s, rung=rung, passes=rec["passes"])
                self.checkpoint()


def cmd_ladder(name, smoke_id=None, pushed_at=None, use_brain=True):
    """Decision or declared smoke run of the ladder on one target, under the pushed manifest."""
    from model_moving_day import agent
    man, sha = mf.load()
    problems = mf.verify(man, name, CLONES)
    if problems:
        raise mf.ManifestMismatch(problems)
    push = mf.pushed()
    entry, rules = man["targets"][name], man["rules"]
    if smoke_id:
        smoke = next((x for x in man["smoke"] if x["id"] == smoke_id and x["target"] == name), None)
        if smoke is None:
            raise mf.ManifestMismatch([f"smoke run {smoke_id} on {name} is not declared in the manifest"])
        k, rungs, kind = smoke["k"], [r for r in RUNGS if r[0] in smoke["rungs"]], "smoke"
    else:
        k, rungs, kind = rules["k"], RUNGS, "decision"
    box, led = sb.Sandbox(), tf.Ledger()
    brain = agent.Brain(tf.Client("c1"), agent.pick_brain(CLONES / name), out_dir(name) / "agent-steps.jsonl") \
        if use_brain and kind == "decision" else None
    run = Ladder(name, entry, rules, k, rungs, kind, out_dir(name), runner.run_job, brain=brain, box=box, ledger=led,
                 manifest={"sha256": sha, "pushed_at": pushed_at, **push}, smoke_id=smoke_id,
                 cap=man["budgets"]["cap_usd_per_job"], spans=find.site_spans(ast_sites(name).sites))
    doc = run.run()
    print(json.dumps({s: (v["status"], v["chosen"], v["reason"]) for s, v in doc["sites"].items()}))
    return doc


def latest_ladder(name, kind="decision"):
    """The newest complete ladder run of this kind for a target."""
    for p in sorted(out_dir(name).glob("ladder-*.json"), reverse=True):
        d = json.loads(p.read_text())
        if d.get("kind") == kind and d.get("status") == "complete":
            return d, p
    raise FileNotFoundError(f"no complete {kind} ladder run for {name} in {out_dir(name)}")


def cmd_manifest(names, reason):
    runs_log = sb.DEFAULT_LOG
    logged = sum(1 for line in open(runs_log) if line.strip()) if runs_log.exists() else 0
    previous = mf.load()[0] if mf.PATH.exists() else None
    man = mf.build(names, CLONES, reason, {
        "logged_when_written": logged, "planned_phase_b": 48,
        "plan": "instructor ladder <= 8, llm ladder <= 8, third repo prepare + controls + ladder <= 13, patch confirm 3, "
                "fallback cells, smoke 1-2",
        "limit": "MMD_SANDBOX_MAX_RUNS, set by the orchestrator before the first ladder job (default in code 40)"},
        previous)
    sha = mf.write(man)
    print(json.dumps({"manifest": str(mf.PATH.relative_to(ROOT)), "sha256": sha, "targets": names}))
    return sha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "control", "coverage", "manifest", "ladder", "patch"])
    ap.add_argument("target", nargs="?", default="")
    ap.add_argument("--which", default="forward,broken")
    ap.add_argument("--cap", type=float, default=0.50, help="control runs only; the ladder takes it from the manifest")
    ap.add_argument("--smoke", default=None, help="id of a smoke run declared in the manifest")
    ap.add_argument("--pushed-at", default=None, help="UTC time GitHub recorded for the manifest push")
    ap.add_argument("--targets", default="instructor,llm")
    ap.add_argument("--reason", default="")
    a = ap.parse_args()
    if a.cmd == "prepare":
        cmd_prepare(a.target)
    elif a.cmd == "control":
        cmd_control(a.target, a.which.split(","), a.cap)
    elif a.cmd == "manifest":
        if not a.reason:
            ap.error("--reason is required: every manifest version says why it was written")
        cmd_manifest([x for x in a.targets.split(",") if x], a.reason)
    elif a.cmd == "ladder":
        cmd_ladder(a.target, a.smoke, a.pushed_at)
    elif a.cmd == "patch":
        from model_moving_day import patch
        patch.cmd_patch(a.target)
    else:
        cmd_coverage(a.target)


if __name__ == "__main__":
    main()
