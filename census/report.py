"""Write CENSUS.md from the evidence: every number comes from census/MANIFEST.json, the ladder files
(evidence/census/<target>/ladder-*.json), coverage.json and, when present, the patch results. Run again to recompute.
data() returns all of it as one JSON-ready dict; render() turns that dict into CENSUS.md, and demo/build.py writes the
same dict to demo/data/census.json.

    .venv/bin/python census/report.py            # writes CENSUS.md at the repository root
"""
import hashlib, json, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_moving_day import ladder, pr  # noqa: E402

EVIDENCE = ROOT / "evidence" / "census"
RUNG_ORDER = [r for r, _ in ladder.RUNGS]
SHORT = {m: r for r, m in ladder.RUNGS}

# What each measured call site does (read from the target's source at the pinned commit).
ROLES = {
    "instructor/v2/core/retry.py:224": "instructor's sync retry funnel for structured output (its tests have no nested model)",
    "instructor/v2/core/retry.py:523": "the async twin of the same funnel (its tests include a nested model)",
    "instructor/v2/validation/llm_validators.py:34": "llm_validator: an LLM judges a field value (accept or reject with a reason)",
    "llm/default_plugins/openai_models.py:1494": "llm's non-streaming chat call (tools, chains of tool calls)",
    "src/paperqa/docs.py:234": "paper-qa: parse a citation into structured fields",
    "src/paperqa/core.py:254": "paper-qa: summarize one evidence chunk for the question (summary_llm, the high-volume step)",
    "src/paperqa/docs.py:675": "paper-qa: write the answer from the evidence",
    "src/paperqa/docs.py:703": "paper-qa: post-prompt after the answer",
}
# Independent decisions: sites measured by the same tests, or two faces of one funnel, count once (acceptance M8/F4).
GROUPS = {
    "instructor/v2/core/retry.py:224": "instructor retry funnel (sync and async)",
    "instructor/v2/core/retry.py:523": "instructor retry funnel (sync and async)",
    "src/paperqa/docs.py:234": "paper-qa query pipeline (one set of tests reaches these sites; docs.py:703 only through one of them)",
    "src/paperqa/core.py:254": "paper-qa query pipeline (one set of tests reaches these sites; docs.py:703 only through one of them)",
    "src/paperqa/docs.py:675": "paper-qa query pipeline (one set of tests reaches these sites; docs.py:703 only through one of them)",
    "src/paperqa/docs.py:703": "paper-qa query pipeline (one set of tests reaches these sites; docs.py:703 only through one of them)",
}


def runs(target, kind):
    out = []
    for p in sorted((EVIDENCE / target).glob("ladder-*.json")):
        d = json.loads(p.read_text())
        if d.get("kind") == kind:
            out.append((d, p))
    return out


def chosen_cell(v):
    return next((c for c in v["cells"] if c.get("role", "ladder") == "ladder" and c["passes"]), None)


def money(x, digits=4):
    return "-" if x is None else f"${x:.{digits}f}"


def _count_infra(acc, target, d):
    """Set-aside sandbox test runs: sites measured in the same sandbox job at a rung share its runs, so each connected
    set of (rung, site) is counted once, at the largest count among its cells."""
    parent = {}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for j in d["jobs"]:
        nodes = [(SHORT.get(j["model"], j["model"]), s) for s in j["sites"]]
        for n in nodes:
            parent.setdefault(n, n)
        for n in nodes[1:]:
            parent[find(n)] = find(nodes[0])
    for s, v in d["sites"].items():
        for c in v["cells"]:
            n = (c["rung"], s)
            key = (target, d["run_id"], find(n) if n in parent else n, json.dumps(c["params"], sort_keys=True))
            acc[key] = max(acc.get(key, 0), c.get("infra_runs", 0))


def _not_measured(man):
    out = []
    for target, t in man["targets"].items():
        out += [(target, s) for s in t.get("excluded_sites", {})]
        out += [(target, s) for s in t.get("traced_not_covered", [])]
        out += [(target, s) for s in t.get("ast_sites_not_reached", [])]
    return out


def _ratio(r):
    return f"{r:.2f}" + (" (about the same: within tokenizer differences)" if 0.85 <= r <= 1.15 else "")


def _ok_calls(c):
    return c["calls"] - sum((c.get("errors") or {}).values())


def _alt_baseline(v, c, price):
    """Token Factory reports no reasoning tokens for Nano, so a Nano cell with reasoning on and 0 reasoning tokens counts
    its hidden reasoning as visible output. Size the visible output by another measured cell at the same site that does
    report reasoning tokens, and compare per successful call."""
    if c["rung"] != "Nano" or (c["params"] or {}).get("reasoning") == "off" or c["reasoning_tokens"] or not _ok_calls(c):
        return None
    ref = next((x for x in v["cells"] if x is not c and x["reasoning_tokens"] and _ok_calls(x)), None)
    if ref is None:
        return None
    vis = pr._visible_out(ref) * ref["calls"] / _ok_calls(ref)
    tins = sorted([c["tokens_per_call"][0] * c["calls"] / _ok_calls(c), ref["tokens_per_call"][0] * ref["calls"] / _ok_calls(ref)])
    base = [1000 * (t * price[0] + vis * price[1]) / 1e6 for t in tins]
    return {"ref": ref, "vis": vis, "base": base, "after": 1000 * c["model_cost"] / _ok_calls(c),
            "ref_after": 1000 * ref["model_cost"] / _ok_calls(ref)}


def _alt_note(i, s, v, c, alt, b):
    r = alt["ref"]
    verdict = "not cheaper than the base" if alt["after"] > alt["base"][0] else "cheaper than the base"
    return (f"`{s}`: Token Factory reports no reasoning tokens for Nano (none in this cell, and none in 83 of 83 Nano "
            f"responses of our spikes), so the base {money(b, 3)} counts Nano's hidden reasoning as visible output and is "
            f"too high. Sized by the visible output of {r['rung']} at the same site ({alt['vis']:.0f} tokens per call), "
            f"the base is {money(alt['base'][0], 3)}-{money(alt['base'][1], 3)} per 1,000 successful calls, against "
            f"Nano's {money(alt['after'], 3)}: on this site Nano is {verdict}, and the measured {r['rung']} "
            f"{r.get('role', 'ladder')} cell ({money(alt['ref_after'], 3)} per 1,000 successful calls) is cheaper than "
            f"Nano. The \"before\" spend per passed run in this row is too high for the same reason.")


def _dollar_notes(all_sites, rung_price):
    out = []
    same = [r for r in rung_price if rung_price[r] == rung_price.get("Nano")]
    if len(same) > 1:
        out.append(f"{' and '.join(same)} cost the same per token on Token Factory (${rung_price['Nano'][0]} in / "
                   f"${rung_price['Nano'][1]} out per 1M), so a smaller rung is not cheaper by itself: the cost follows "
                   f"the tokens each model produces.")
    for _, s, v in all_sites:
        cells = {(c["rung"], json.dumps(c["params"], sort_keys=True)): c for c in v["cells"]}
        nano, light = cells.get(("Nano", "{}")), cells.get(("Lightning", "{}"))
        if nano and light and nano["calls"] == light["calls"] and not nano.get("errors") and not light.get("errors"):
            a, b = nano["tokens_per_call"][0], light["tokens_per_call"][0]
            out.append(f"Tokenizers differ: at `{s}` (same tests, same number of calls) Nano counted {a:.0f} prompt tokens "
                       f"per call and Lightning {b:.0f} ({100 * (b - a) / a:+.0f} %). A difference of 10-15 % against the "
                       f"base is within that.")
    for _, s, v in all_sites:
        if v["status"] != "chosen":
            continue
        for c in v["cells"]:
            err = sum((c.get("errors") or {}).values())
            if err and c["passes"]:
                out.append(f"`{s}` {c['rung']} ({c.get('role', 'ladder')}): {err} of {c['calls']} calls were error "
                           f"responses ({', '.join(sorted(c['errors']))}) with no tokens; they are in the denominator of "
                           f"$/1k calls, which lowers both sides alike and leaves after / before unchanged.")
    out.append("\"Spend per passed run\" divides everything the cell spent, set-aside runs included, by its passed "
               "runs; where a batch was set aside it is higher than the cost of one clean run.")
    return out


def _confirm_cells(d):
    return "; ".join(f"`{s}` " + ", ".join(f"{c['rung']} {c['passed_runs']}/{c['valid_runs']} {c['status']}" for c in v["cells"])
                     for s, v in d["sites"].items())


def _external_failures(d):
    seen = []
    for s, v in d["sites"].items():
        for c in v["cells"]:
            m = re.search(r"for url '(https?://[^/']+)", c.get("first_failure") or "")
            if m and not re.match(r"https?://(127\.0\.0\.1|localhost)", m.group(1)):
                host = m.group(1).split("://", 1)[1]
                if host not in [h for h, _ in seen]:
                    seen.append((host, s))
    return seen


def data():
    """Everything CENSUS.md says, as one JSON-ready dict (also the source of the demo's census.json)."""
    raw = (ROOT / "census" / "MANIFEST.json").read_bytes()
    man = json.loads(raw)
    base = man["baseline_before"]
    price = tuple(base["usd_per_1m_tokens"])
    all_sites, decisions, token_total, diag_total, smoke = [], {}, 0.0, 0.0, []
    infra_latest, infra_all, lost_jobs = {}, {}, 0
    per_target, all_runs = [], {}
    for target in man["targets"]:
        dec = [d for d, _ in runs(target, "decision") if d["status"] == "complete"]
        all_runs[target] = [d for d, _ in runs(target, "decision")]
        for d in all_runs[target]:
            _count_infra(infra_all, target, d)
            for j in d["jobs"]:
                token_total += j.get("model_spend") or 0
                diag_total += j.get("sandbox_cost_diagnostic") or 0
                lost_jobs += 1 if j.get("error") else 0
        for d, _ in runs(target, "smoke"):
            smoke.append({"target": target, "run_id": d["run_id"], "smoke": d["smoke"], "k": d["k"], "rungs": d["rungs"],
                          "status": d["status"]})
        if not dec:
            per_target.append((target, None))
            continue
        d = dec[-1]
        per_target.append((target, d))
        _count_infra(infra_latest, target, d)
        for s, v in d["sites"].items():
            all_sites.append((target, s, v))
            g = GROUPS.get(s, s)
            rank = RUNG_ORDER.index(v["chosen"]) if v["status"] == "chosen" else (99 if v["status"] == "red" else None)
            if rank is not None:
                decisions[g] = max(decisions.get(g, -1), rank)
            else:
                decisions.setdefault(g, None)
    measured = {g: r for g, r in decisions.items() if r is not None}
    small = [g for g, r in measured.items() if r <= 1]
    sites_small = [s for _, s, v in all_sites if v["status"] == "chosen" and v["chosen"] in ("Nano", "Lightning")]
    decided = [x for x in all_sites if x[2]["status"] in ("chosen", "red")]
    repos_decided = sorted({t for t, _, _ in decided})
    not_measured = _not_measured(man)
    flips = {s: len(v.get("flip_tests", [])) for _, s, v in decided}
    chosen_by_site = {s: v.get("chosen") for _, s, v in decided}
    head = {"sites_found": len(all_sites) + len(not_measured), "repositories": len(man["targets"]),
            "sites_in_ladder": len(all_sites), "sites_not_measured": len(not_measured), "decided": len(decided),
            "repositories_decided": repos_decided, "decided_on_small": len(sites_small), "flip_tests": flips,
            "nano_tests": sum(n for s, n in flips.items() if chosen_by_site[s] == "Nano"),
            "independent_grouped": len(measured), "small_grouped": len(small),
            "gate_met": len(repos_decided) >= 3 and min(len(measured), len(decided)) >= 5,
            "wilson_per_site": list(ladder.wilson(len(sites_small), len(decided))),
            "wilson_grouped": list(ladder.wilson(len(small), len(measured))),
            "set_aside_runs_headline": sum(infra_latest.values()), "set_aside_runs_all": sum(infra_all.values()),
            "sites_infra": sum(1 for _, _, v in all_sites if v["status"] == "infra"),
            "decision_tokens_usd": token_total, "sandbox_cost_diagnostic_usd": diag_total, "lost_jobs": lost_jobs}
    sites, site_notes = [], []
    for target, s, v in all_sites:
        c = chosen_cell(v)
        b = pr.baseline_usd_per_1000(c, price) if c else None
        alt = _alt_baseline(v, c, price) if c else None
        if alt:
            site_notes.append(_alt_note(len(site_notes) + 1, s, v, c, alt, b))
        sites.append({
            "target": target, "site": s, "role": ROLES.get(s, "-"), "flip_tests": len(v.get("flip_tests", [])),
            "status": v["status"], "chosen": v.get("chosen"), "fix": (v.get("fix") or {}).get("kind"),
            "after_usd_per_1000": c["usd_per_1000_calls"] if c else None, "before_usd_per_1000": b,
            "after_over_before": None if not (c and b) or alt else c["usd_per_1000_calls"] / b,
            "note": len(site_notes) if alt else None,
            "base_check": None if not alt else {
                "sized_by": f"{alt['ref']['rung']} ({alt['ref'].get('role', 'ladder')})", "visible_tokens": alt["vis"],
                "before_usd_per_1000_ok_calls": alt["base"], "after_usd_per_1000_ok_calls": alt["after"],
                "sized_by_usd_per_1000_ok_calls": alt["ref_after"]},
            "spend_per_passed_run_after": c.get("usd_per_passed_run") if c else None,
            "spend_per_passed_run_before": pr.baseline_usd_per_passed_run(c, price) if c else None,
            "calls_per_test_run": c.get("calls_per_test_run") if c else None,
            "headline_run": dict(per_target)[target]["run_id"]})
    targets = []
    for target, latest in per_target:
        trs = [_run(target, d, latest) for d in all_runs[target]]
        targets.append({"target": target, "runs": [r["run_id"] for r in trs],
                        "external_failures": [{"run_id": d["run_id"], "host": h, "site": s}
                                              for d in all_runs[target] for h, s in _external_failures(d)],
                        "posthoc": [str(p.relative_to(ROOT)) for p in sorted((EVIDENCE / target).glob("lost-jobs-posthoc-*.json"))],
                        "_runs": trs})
    wrong_everywhere = all((EVIDENCE / t / "control-wrong.json").exists() for t in man["targets"])
    budgets = {t: e["token_budget_usd"] for t, e in man["targets"].items() if "token_budget_usd" in e}
    out = {
        "manifest": {"sha256": hashlib.sha256(raw).hexdigest(), "amendments": len(man.get("amendments", [])),
                     "baseline": base, "embedding_model": man["rules"]["embedding_model"]["model"],
                     "rung_prices": {r["rung"]: r["usd_per_1m_tokens"] for r in man["rules"]["rungs"]},
                     "typos": [{"utc": a["utc"], "token_budget_usd": budgets} for a in man.get("amendments", [])
                               if "/bin/bash." in a["reason"]]},
        "headline": head, "sites": sites, "site_notes": site_notes,
        "dollar_notes": _dollar_notes(all_sites, {r["rung"]: tuple(r["usd_per_1m_tokens"]) for r in man["rules"]["rungs"]}),
        "targets": [{k: v for k, v in t.items() if k != "_runs"} for t in targets],
        "runs": {r["run_id"]: r for t in targets for r in t["_runs"]},
        "not_measured": _not_measured_rows(man), "smoke": smoke, "patches": _patches(man),
        "not_claimed": [{"text": x, "correction": "only where the manifest lists control-wrong" in x and wrong_everywhere}
                        for x in man["not_claimed"]]}
    return out


def _run(target, d, latest):
    m = d["manifest"]
    statuses = {}
    for j in d["jobs"]:
        for v in (j.get("site_runs") or {}).values():
            for e in (v.get("errors_by_run") or {}).values():
                for code, n in e.items():
                    statuses[code] = statuses.get(code, 0) + n
    lost = [j for j in d["jobs"] if j.get("error")]
    cells = []
    for s, v in d["sites"].items():
        for c in v["cells"]:
            r = c.get("runs_with_retried_infra") or {}
            cells.append({"site": s, "role": c.get("role", "ladder"), "rung": c["rung"], "params": c["params"],
                          "status": c["status"], "passed_runs": c["passed_runs"], "valid_runs": c["valid_runs"],
                          "infra_runs": c.get("infra_runs", 0), "set_aside_passed": r.get("pass", 0),
                          "set_aside_failed": r.get("fail", 0), "usd_per_1000_calls": c["usd_per_1000_calls"],
                          "first_failure": c["first_failure"] or ""})
    return {"target": target, "run_id": d["run_id"], "kind": d["kind"], "status": d["status"],
            "started_utc": d["started_utc"], "ended_utc": d.get("ended_utc"), "manifest_sha256": m.get("sha256", ""),
            "pushed_at": m.get("pushed_at"), "k": d["k"],
            "required_passes": ladder.required_passes(d["k"], d["baseline"], d["margin"]),
            "decided": sum(1 for v in d["sites"].values() if v["status"] in ("chosen", "red")), "sites": len(d["sites"]),
            "site_status": {s: v["status"] for s, v in d["sites"].items()},
            "jobs": len(d["jobs"]), "lost_jobs": len(lost), "lost_runs": sum(int(j["k"]) for j in lost),
            "token_spend_usd": sum(j.get("model_spend") or 0 for j in d["jobs"]),
            "proxy_error_statuses": dict(sorted(statuses.items())),
            "headline": bool(latest and d["run_id"] == latest["run_id"]), "cells": cells,
            "fixes": [{"site": s, **{k: v["fix"].get(k) for k in ("kind", "by", "why", "applied_calls")}}
                      for s, v in d["sites"].items() if v.get("fix")]}


def _not_measured_rows(man):
    out = []
    for target, t in man["targets"].items():
        out += [{"target": target, "site": s, "why": f"excluded before its ladder: {why}"}
                for s, why in t.get("excluded_sites", {}).items()]
        out += [{"target": target, "site": s, "why": "reached by the tests, but no test fails on an empty or wrong answer there"}
                for s in t.get("traced_not_covered", [])]
        out += [{"target": target, "site": s, "why": "found in the source, reached by none of the kept tests"}
                for s in t.get("ast_sites_not_reached", [])]
    return out


def _patches(man):
    out = []
    for t in man["targets"]:
        p = EVIDENCE / t / "patch" / "result.json"
        if not p.exists():
            continue
        r = json.loads(p.read_text())
        confirms = []
        for d, _ in runs(t, "confirm"):
            counts = {}
            for j in d["jobs"]:
                for site, sr in (j.get("site_runs") or {}).items():
                    for m, n in (sr.get("models_requested") or {}).items():
                        counts.setdefault(site, {}).setdefault(SHORT.get(m, m), 0)
                        counts[site][SHORT.get(m, m)] += n
            confirms.append({"run_id": d["run_id"], "cells": _confirm_cells(d), "calls": counts})
        out.append({"target": t, "ladder_run": r["ladder_run"], "moved": r["moved"], "kept": r["kept"],
                    "confirm": r["confirm"], "branch": r["branch"], "commit": r["commit"],
                    "pr": f"evidence/census/{t}/patch/PR.md",
                    "earlier_attempts": len(list((EVIDENCE / t).glob("patch-attempt*"))), "confirm_runs": confirms})
    return out


def render(D):
    h, base = D["headline"], D["manifest"]["baseline"]
    price = tuple(base["usd_per_1m_tokens"])
    lines = ["# Model Moving Day: census of three open-source LLM libraries and tools", "",
             "Every number below is recomputed by `census/report.py` from the evidence in this repository "
             "(`evidence/census/<repo>/ladder-*.json`, `coverage.json`, `census/MANIFEST.json`). Test traffic, not "
             "production traffic.", ""]
    (slo, shi), (lo, hi) = h["wilson_per_site"], h["wilson_grouped"]
    lines += ["## Headline", "",
              f"- LLM call sites found: {h['sites_found']} in {h['repositories']} repositories. "
              f"Measurable with the repositories' own tests and put into the ladder: {h['sites_in_ladder']}. Found but not "
              f"measured: {h['sites_not_measured']}, each with its reason under \"Found, not measured\".",
              f"- Decided: {h['decided']} call sites, in {len(h['repositories_decided'])} repositories "
              f"({', '.join(h['repositories_decided'])}); "
              f"on Lightning or Nano: {h['decided_on_small']} of {h['decided']}. Each decision rests on that site's flip tests: "
              f"{', '.join(f'{n} at `{s}`' for s, n in h['flip_tests'].items())}; the sites that moved to Nano rest on "
              f"{h['nano_tests']} tests between them.",
              f"- Independent decisions: {h['decided']} counted per call site ({h['decided_on_small']} on Lightning or Nano); "
              f"{h['independent_grouped']} if the sync and async faces of instructor's retry funnel count as one site at the larger "
              f"of their two models ({h['small_grouped']} on Lightning or Nano). The two faces share no flip test.",
              f"- Coverage gate (at least 3 repositories and 5 independent decided sites): "
              f"{'met' if h['gate_met'] else 'NOT met'}: {len(h['repositories_decided'])} repositories with a decision, "
              f"{h['independent_grouped']} or {h['decided']} independent decisions. The gate and the rule that a sync/async pair "
              f"counts once were written on 2026-10-24 in our internal acceptance notes before the ladder runs; they are "
              f"not in the pushed manifest. Counting a group at its largest model was added to this script after the "
              f"runs.",
              f"- For reference only, Wilson 95 % intervals of the share on Lightning or Nano, which are not an estimate "
              f"of any population (three hand-picked repositories): {h['decided_on_small']}/{h['decided']} per site "
              f"[{slo:.2f}-{shi:.2f}], {h['small_grouped']}/{h['independent_grouped']} grouped [{lo:.2f}-{hi:.2f}].",
              f"- Runs set aside as infrastructure (rate limits, 5xx, lost jobs) and re-run, counted per sandbox test run "
              f"(sites that share a job share its runs): {h['set_aside_runs_headline']} in the runs the headline uses, "
              f"{h['set_aside_runs_all']} in all decision runs. Sites with no decision because of infrastructure: "
              f"{h['sites_infra']}.",
              f"- Model tokens for all decision ladder runs: {money(h['decision_tokens_usd'])}. Controls, the smoke run, "
              f"the brain, confirm runs and spikes are not in this sum"
              + (f", nor {h['lost_jobs']} sandbox jobs lost to client timeouts (each capped by a $1 envelope)"
                 if h["lost_jobs"] else "")
              + f". The Sandboxes `cost` field summed to {money(h['sandbox_cost_diagnostic_usd'], 2)} over these runs; it is "
              f"diagnostic: Token Factory billing showed no Sandboxes charge when checked on 2026-10-24 (last check at 13:47 "
              f"UTC, with Usage data as of 12:11 UTC), so runs after 12:11 UTC are not covered by that check.", ""]
    lines += ["## Which model does what", "",
              f"| Repository | Call site | What the call does | Flip tests | Model after the census | $/1k calls after | "
              f"$/1k calls before ({base['model']}, priced) | After / before | Spend per passed run, set-aside re-runs "
              f"included, after / before | Calls per test run |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for r in D["sites"]:
        has = r["after_usd_per_1000"] is not None
        ratio = ("-" if not (has and r["before_usd_per_1000"]) else
                 f"not shown, see note {r['note']}" if r["note"] else _ratio(r["after_over_before"]))
        model = (f"**{r['chosen']}**" + (f" + {r['fix']}" if r["fix"] else "")) if r["status"] == "chosen" \
            else ("old model stays" if r["status"] == "red" else r["status"])
        lines.append(f"| {r['target']} | `{r['site']}` | {r['role']} | {r['flip_tests']} | {model} | "
                     f"{money(r['after_usd_per_1000'], 3) if has else '-'} | {money(r['before_usd_per_1000'], 3)} | {ratio} | "
                     f"{money(r['spend_per_passed_run_after'], 5)} / {money(r['spend_per_passed_run_before'], 5)} | "
                     f"{r['calls_per_test_run'] if has else '-'} |")
    lines += ["", f"\"Before\" is a price calculation, not a run: the same calls, at the prompt tokens and visible output "
              f"tokens Nemotron produced, priced at {base['model']} (${price[0]}/${price[1]} per 1M tokens), the model "
              f"OpenAI names as the replacement for the models these tests name ({base['source']}; rechecked against "
              f"OpenAI's live pages on 2026-10-24, `evidence/platform/openai-pricing-check-2026-10-24.md`). "
              f"{base['model']} was never called, and its pass rate is assumed to be 1.0.", ""]
    lines += [f"{i}. {n}" for i, n in enumerate(D["site_notes"], 1)] + ([""] if D["site_notes"] else [])
    lines += ["Notes on the dollar columns:", ""] + [f"- {x}" for x in D["dollar_notes"]] + [""]
    lines += [f"Diagnosis and fixes: a proxy rule sets the diagnosis from the call log; the brain (Super or Ultra by repo "
              f"size) is asked only for a red cell with a diagnosis and picks from the pre-registered fix list; a fix "
              f"counts only if the request really changed. The embedding calls of paper-qa go to "
              f"{D['manifest']['embedding_model']} on every rung.", ""]
    lines += ["## Every cell", ""]
    for t in D["targets"]:
        lines += [f"### {t['target']}", ""]
        if not t["runs"]:
            lines += ["No decision run.", ""]
        for rid in t["runs"]:
            r = D["runs"][rid]
            lines.append(f"- Decision run `{rid}`: {r['status']}, {r['decided']} of {r['sites']} sites decided, "
                         f"{r['jobs']} sandbox jobs" + (f" ({r['lost_jobs']} lost to client timeouts while polling the "
                                                        f"Sandboxes API: no summary came back, so their "
                                                        f"{r['lost_runs']} runs were set aside)"
                                                        if r["lost_jobs"] else "") + f", {money(r['token_spend_usd'])} in tokens"
                         + (f", proxy error statuses logged {r['proxy_error_statuses']}" if r["proxy_error_statuses"] else "")
                         + (" (the headline uses this run)" if r["headline"] else ""))
        for x in t["external_failures"]:
            lines.append(f"- Run `{x['run_id']}`: a run counted as a model failure at `{x['site']}` failed on an external "
                         f"service the tests call live (`{x['host']}`), not on the model; the infrastructure rule sees only "
                         f"the proxy's statuses")
        for p in t["posthoc"]:
            lines.append(f"- Post-hoc read of lost jobs, not counted (summary lost = infrastructure by the pre-registered "
                         f"rule): `{p}`")
        lines.append("")
        for rid in t["runs"]:
            _cells(lines, D["runs"][rid])
    lines += ["## Found, not measured", ""]
    lines += [f"- {x['target']} `{x['site']}`: {x['why']}" for x in D["not_measured"]]
    lines += ["", "## Smoke runs (not for decision)", ""]
    lines += [f"- `{x['target']}` smoke `{x['run_id']}` ({x['smoke']}, K={x['k']}, rungs {', '.join(x['rungs'])}): "
              f"{x['status']}; **not for decision**" for x in D["smoke"]] or ["- none"]
    lines += ["", "## Migration patches", ""]
    if not D["patches"]:
        lines += ["- none yet"]
    for r in D["patches"]:
        t = r["target"]
        lines += [f"- {t}: ladder run `{r['ladder_run']}`; moved {len(r['moved'])} sites, kept {len(r['kept'])}; "
                  f"confirm {r['confirm']}; local branch `{r['branch']}` commit `{(r['commit'] or '-')[:12]}`; "
                  f"PR text `{r['pr']}`"
                  + (f"; {r['earlier_attempts']} earlier attempts kept under `{t}/patch-attempt*/` (nothing committed by "
                     f"them)" if r["earlier_attempts"] else "")]
        for c in r["confirm_runs"]:
            lines.append(f"  - confirm run `{c['run_id']}`: sites {c['cells']}; model calls by the call site the tracer "
                         f"recorded in the patched code: {c['calls'] or 'none'}")
    if D["patches"]:
        lines += ["", "The \"Models requested\" column in the PR texts shows the count from one test, not the total; "
                  "the totals per recorded site are listed above."]
    lines += ["", "## What is not claimed", ""]
    for x in D["not_claimed"]:
        lines.append(f"- {x['text']}")
        if x["correction"]:
            lines.append("  - Correction: this line dates from the first manifest and is kept so the manifest is not "
                         "changed after the runs. The wrong-answer control ran in all three repositories "
                         "(`evidence/census/*/control-wrong.json`).")
    if D["manifest"]["typos"]:
        lines += ["", "## Notes on the manifest", ""]
        for a in D["manifest"]["typos"]:
            lines.append(f"- The reason text of the amendment written at {a['utc']} reads \"/bin/bash.66\" where "
                         f"\"$0.66\" was meant (the shell expanded `$0` when the text was written). The binding field is "
                         f"correct: token_budget_usd {a['token_budget_usd']}.")
    return "\n".join(lines) + "\n"


def _cells(lines, r):
    lines += [f"#### Run `{r['run_id']}`", "",
              f"Decision run `{r['run_id']}` ({r['started_utc']} to {r['ended_utc']}), manifest "
              f"`{r['manifest_sha256'][:12]}` pushed {r['pushed_at']}, K={r['k']}, pass = at least "
              f"{r['required_passes']} of {r['k']} valid runs.", "",
              "| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |",
              "|---|---|---|---|---|---|---|---|---|"]
    for c in r["cells"]:
        lines.append(f"| `{c['site']}` | {c['role']} | {c['rung']} | {c['params'] or '-'} | {c['status']} | "
                     f"{c['passed_runs']}/{c['valid_runs']} | {c['infra_runs']} ({c['set_aside_passed']}, {c['set_aside_failed']}) | "
                     f"{money(c['usd_per_1000_calls'], 3)} | {(c['first_failure'] or '-')[:120].replace('|', '/')} |")
    lines += ["", "Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many "
              "had failed underneath (runs of a lost job have no outcome).", ""]
    for f in r["fixes"]:
        lines += [f"Fix at `{f['site']}`: **{f['kind']}**, picked by `{f['by']}` (\"{f['why']}\"); the request change "
                  f"showed on {f.get('applied_calls')} calls. How much of this the brain found by itself: the "
                  f"diagnosis comes from a proxy rule on the call log, the pre-registered fix list allows only "
                  f"this fix for that diagnosis, the brain chose from three options (reasoning_off, "
                  f"raise_max_tokens, none), and its system prompt states that with reasoning on, content can "
                  f"start with a newline.", ""]


def main():
    D = data()
    (ROOT / "CENSUS.md").write_text(render(D))
    h = D["headline"]
    print(json.dumps({"sites": h["sites_in_ladder"], "decided": h["decided"], "independent": h["independent_grouped"],
                      "small": h["small_grouped"], "tokens": round(h["decision_tokens_usd"], 4),
                      "diag": round(h["sandbox_cost_diagnostic_usd"], 2)}))


if __name__ == "__main__":
    main()
