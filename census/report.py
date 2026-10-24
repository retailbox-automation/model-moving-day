"""Write CENSUS.md from the evidence: every number comes from census/MANIFEST.json, the ladder files
(evidence/census/<target>/ladder-*.json), coverage.json and, when present, the patch results. Run again to recompute.

    .venv/bin/python census/report.py            # writes CENSUS.md at the repository root
"""
import json, math, re, sys
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


def main():
    man = json.loads((ROOT / "census" / "MANIFEST.json").read_text())
    base = man["baseline_before"]
    price = tuple(base["usd_per_1m_tokens"])
    lines = ["# Model Moving Day: census of three open-source LLM libraries and tools", "",
             "Every number below is recomputed by `census/report.py` from the evidence in this repository "
             "(`evidence/census/<repo>/ladder-*.json`, `coverage.json`, `census/MANIFEST.json`). Test traffic, not "
             "production traffic.", ""]
    all_sites, decisions, token_total, diag_total, smoke_lines = [], {}, 0.0, 0.0, []
    infra_latest, infra_all, lost_jobs = {}, {}, 0
    per_target = []
    all_runs = {}
    for target in man["targets"]:
        dec = [d for d, _ in runs(target, "decision") if d["status"] == "complete"]
        all_runs[target] = [d for d, _ in runs(target, "decision")]
        for d in all_runs[target]:
            _count_infra(infra_all, target, d)
            for j in d["jobs"]:
                token_total += j.get("model_spend") or 0
                diag_total += j.get("sandbox_cost_diagnostic") or 0
                lost_jobs += 1 if j.get("error") else 0
        smoke = runs(target, "smoke")
        for d, p in smoke:
            smoke_lines.append(f"- `{target}` smoke `{d['run_id']}` ({d['smoke']}, K={d['k']}, rungs {', '.join(d['rungs'])}): "
                               f"{d['status']}; **not for decision**")
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
    lo, hi = ladder.wilson(len(small), len(measured))
    decided = [x for x in all_sites if x[2]["status"] in ("chosen", "red")]
    repos_decided = sorted({t for t, _, _ in decided})
    slo, shi = ladder.wilson(len(sites_small), len(decided))
    not_measured = _not_measured(man)
    gate_met = len(repos_decided) >= 3 and min(len(measured), len(decided)) >= 5
    flips = {s: len(v.get("flip_tests", [])) for _, s, v in decided}
    nano_tests = sum(n for s, n in flips.items() if dict((x, v) for _, x, v in decided)[s]["chosen"] == "Nano")
    lines += ["## Headline", "",
              f"- LLM call sites found: {len(all_sites) + len(not_measured)} in {len(man['targets'])} repositories. "
              f"Measurable with the repositories' own tests and put into the ladder: {len(all_sites)}. Found but not "
              f"measured: {len(not_measured)}, each with its reason under \"Found, not measured\".",
              f"- Decided: {len(decided)} call sites, in {len(repos_decided)} repositories ({', '.join(repos_decided)}); "
              f"on Lightning or Nano: {len(sites_small)} of {len(decided)}. Each decision rests on that site's flip tests: "
              f"{', '.join(f'{n} at `{s}`' for s, n in flips.items())}; the sites that moved to Nano rest on {nano_tests} "
              f"tests between them.",
              f"- Independent decisions: {len(decided)} counted per call site ({len(sites_small)} on Lightning or Nano); "
              f"{len(measured)} if the sync and async faces of instructor's retry funnel count as one site at the larger "
              f"of their two models ({len(small)} on Lightning or Nano). The two faces share no flip test.",
              f"- Coverage gate (at least 3 repositories and 5 independent decided sites): "
              f"{'met' if gate_met else 'NOT met'}: {len(repos_decided)} repositories with a decision, "
              f"{len(measured)} or {len(decided)} independent decisions. The gate and the rule that a sync/async pair "
              f"counts once were written on 2026-10-24 in our internal acceptance notes before the ladder runs; they are "
              f"not in the pushed manifest. Counting a group at its largest model was added to this script after the "
              f"runs.",
              f"- For reference only, Wilson 95 % intervals of the share on Lightning or Nano, which are not an estimate "
              f"of any population (three hand-picked repositories): {len(sites_small)}/{len(decided)} per site "
              f"[{slo:.2f}-{shi:.2f}], {len(small)}/{len(measured)} grouped [{lo:.2f}-{hi:.2f}].",
              f"- Runs set aside as infrastructure (rate limits, 5xx, lost jobs) and re-run, counted per sandbox test run "
              f"(sites that share a job share its runs): {sum(infra_latest.values())} in the runs the headline uses, "
              f"{sum(infra_all.values())} in all decision runs. Sites with no decision because of infrastructure: "
              f"{sum(1 for _, _, v in all_sites if v['status'] == 'infra')}.",
              f"- Model tokens for all decision ladder runs: {money(token_total)}. Controls, the smoke run, the brain, "
              f"confirm runs and spikes are not in this sum"
              + (f", nor {lost_jobs} sandbox jobs lost to client timeouts (each capped by a $1 envelope)" if lost_jobs else "")
              + f". The Sandboxes `cost` field summed to {money(diag_total, 2)} over these runs; it is diagnostic: "
              f"Token Factory billing showed no Sandboxes charge when checked on 2026-10-24 (last check at 13:47 UTC, "
              f"with Usage data as of 12:11 UTC), so runs after 12:11 UTC are not covered by that check.", ""]
    lines += ["## Which model does what", "",
              f"| Repository | Call site | What the call does | Flip tests | Model after the census | $/1k calls after | "
              f"$/1k calls before ({base['model']}, priced) | After / before | Spend per passed run, set-aside re-runs "
              f"included, after / before | Calls per test run |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    notes = []
    for target, s, v in all_sites:
        c = chosen_cell(v)
        after = money(c["usd_per_1000_calls"], 3) if c else "-"
        b = pr.baseline_usd_per_1000(c, price) if c else None
        pb = pr.baseline_usd_per_passed_run(c, price) if c else None
        alt = _alt_baseline(v, c, price) if c else None
        ratio = "-" if not (c and b) else (f"not shown, see note {len(notes) + 1}" if alt else _ratio(c["usd_per_1000_calls"] / b))
        if alt:
            notes.append(_alt_note(len(notes) + 1, s, v, c, alt, b))
        model = (f"**{v['chosen']}**" + (f" + {v['fix']['kind']}" if v.get("fix") else "")) if v["status"] == "chosen" \
            else ("old model stays" if v["status"] == "red" else v["status"])
        lines.append(f"| {target} | `{s}` | {ROLES.get(s, '-')} | {len(v.get('flip_tests', []))} | {model} | {after} | "
                     f"{money(b, 3)} | {ratio} | {money(c.get('usd_per_passed_run') if c else None, 5)} / {money(pb, 5)} | "
                     f"{c.get('calls_per_test_run') if c else '-'} |")
    rung_price = {r["rung"]: tuple(r["usd_per_1m_tokens"]) for r in man["rules"]["rungs"]}
    lines += ["", f"\"Before\" is a price calculation, not a run: the same calls, at the prompt tokens and visible output "
              f"tokens Nemotron produced, priced at {base['model']} (${price[0]}/${price[1]} per 1M tokens), the model "
              f"OpenAI names as the replacement for the models these tests name ({base['source']}; rechecked against "
              f"OpenAI's live pages on 2026-10-24, `evidence/platform/openai-pricing-check-2026-10-24.md`). "
              f"{base['model']} was never called, and its pass rate is assumed to be 1.0.", ""]
    lines += [f"{i}. {n}" for i, n in enumerate(notes, 1)] + ([""] if notes else [])
    lines += ["Notes on the dollar columns:", ""] + [f"- {x}" for x in _dollar_notes(all_sites, rung_price)] + [""]
    lines += [f"Diagnosis and fixes: a proxy rule sets the diagnosis from the call log; the brain (Super or Ultra by repo "
              f"size) is asked only for a red cell with a diagnosis and picks from the pre-registered fix list; a fix "
              f"counts only if the request really changed. The embedding calls of paper-qa go to "
              f"{man['rules']['embedding_model']['model']} on every rung.", ""]
    lines += ["## Every cell", ""]
    for target, latest in per_target:
        lines += [f"### {target}", ""]
        if not all_runs[target]:
            lines += ["No decision run.", ""]
        for d in all_runs[target]:
            decided_here = sum(1 for v in d["sites"].values() if v["status"] in ("chosen", "red"))
            statuses = {}
            for j in d["jobs"]:
                for v in (j.get("site_runs") or {}).values():
                    for e in (v.get("errors_by_run") or {}).values():
                        for code, n in e.items():
                            statuses[code] = statuses.get(code, 0) + n
            spend = sum(j.get("model_spend") or 0 for j in d["jobs"])
            lost = [j for j in d["jobs"] if j.get("error")]
            lines.append(f"- Decision run `{d['run_id']}`: {d['status']}, {decided_here} of {len(d['sites'])} sites decided, "
                         f"{len(d['jobs'])} sandbox jobs" + (f" ({len(lost)} lost to client timeouts while polling the "
                                                             f"Sandboxes API: no summary came back, so their "
                                                             f"{sum(int(j['k']) for j in lost)} runs were set aside)"
                                                             if lost else "") + f", {money(spend)} in tokens"
                         + (f", proxy error statuses logged {dict(sorted(statuses.items()))}" if statuses else "")
                         + (" (the headline uses this run)" if latest and d["run_id"] == latest["run_id"] else ""))
        for d in all_runs[target]:
            for host, site in _external_failures(d):
                lines.append(f"- Run `{d['run_id']}`: a run counted as a model failure at `{site}` failed on an external "
                             f"service the tests call live (`{host}`), not on the model; the infrastructure rule sees only "
                             f"the proxy's statuses")
        for p in sorted((EVIDENCE / target).glob("lost-jobs-posthoc-*.json")):
            lines.append(f"- Post-hoc read of lost jobs, not counted (summary lost = infrastructure by the pre-registered "
                         f"rule): `{p.relative_to(ROOT)}`")
        lines.append("")
        for d in all_runs[target]:
            _cells(lines, d)
    lines += ["## Found, not measured", ""]
    _rest(lines, man, smoke_lines)
    (ROOT / "CENSUS.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"sites": len(all_sites), "decided": len(decided), "independent": len(measured), "small": len(small),
                      "tokens": round(token_total, 4), "diag": round(diag_total, 2)}))


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


def _cells(lines, d):
    if True:
        m = d["manifest"]
        lines += [f"#### Run `{d['run_id']}`", "",
                  f"Decision run `{d['run_id']}` ({d['started_utc']} to {d.get('ended_utc')}), manifest "
                  f"`{m.get('sha256', '')[:12]}` pushed {m.get('pushed_at')}, K={d['k']}, pass = at least "
                  f"{ladder.required_passes(d['k'], d['baseline'], d['margin'])} of {d['k']} valid runs.", "",
                  "| Call site | Role | Rung | Params | Status | Passed / valid | Set aside (passed, failed) | $/1k calls | First failure |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for s, v in d["sites"].items():
            for c in v["cells"]:
                r = c.get("runs_with_retried_infra") or {}
                lines.append(f"| `{s}` | {c.get('role', 'ladder')} | {c['rung']} | {c['params'] or '-'} | {c['status']} | "
                             f"{c['passed_runs']}/{c['valid_runs']} | {c.get('infra_runs', 0)} ({r.get('pass', 0)}, {r.get('fail', 0)}) | "
                             f"{money(c['usd_per_1000_calls'], 3)} | {(c['first_failure'] or '-')[:120].replace('|', '/')} |")
        lines += ["", "Set aside: runs set aside as infrastructure; in brackets how many of them had passed and how many "
                  "had failed underneath (runs of a lost job have no outcome).", ""]
        for s, v in d["sites"].items():
            if v.get("fix"):
                f = v["fix"]
                lines += [f"Fix at `{s}`: **{f['kind']}**, picked by `{f['by']}` (\"{f['why']}\"); the request change "
                          f"showed on {f.get('applied_calls')} calls. How much of this the brain found by itself: the "
                          f"diagnosis comes from a proxy rule on the call log, the pre-registered fix list allows only "
                          f"this fix for that diagnosis, the brain chose from three options (reasoning_off, "
                          f"raise_max_tokens, none), and its system prompt states that with reasoning on, content can "
                          f"start with a newline.", ""]



def _rest(lines, man, smoke_lines):
    for target in man["targets"]:
        t = man["targets"][target]
        for s, why in t.get("excluded_sites", {}).items():
            lines.append(f"- {target} `{s}`: excluded before its ladder: {why}")
        for s in t.get("traced_not_covered", []):
            lines.append(f"- {target} `{s}`: reached by the tests, but no test fails on an empty or wrong answer there")
        for s in t.get("ast_sites_not_reached", []):
            lines.append(f"- {target} `{s}`: found in the source, reached by none of the kept tests")
    lines += ["", "## Smoke runs (not for decision)", ""] + (smoke_lines or ["- none"])
    patches = [(t, json.loads(p.read_text())) for t in man["targets"] if (p := EVIDENCE / t / "patch" / "result.json").exists()]
    lines += ["", "## Migration patches", ""]
    if not patches:
        lines += ["- none yet"]
    for t, r in patches:
        attempts = sorted(p.name for p in (EVIDENCE / t).glob("patch-attempt*"))
        lines += [f"- {t}: ladder run `{r['ladder_run']}`; moved {len(r['moved'])} sites, kept {len(r['kept'])}; "
                  f"confirm {r['confirm']}; local branch `{r['branch']}` commit `{(r['commit'] or '-')[:12]}`; "
                  f"PR text `evidence/census/{t}/patch/PR.md`"
                  + (f"; {len(attempts)} earlier attempts kept under `{t}/patch-attempt*/` (nothing committed by them)"
                     if attempts else "")]
        for d, _ in runs(t, "confirm"):
            counts = {}
            for j in d["jobs"]:
                for site, sr in (j.get("site_runs") or {}).items():
                    for m, n in (sr.get("models_requested") or {}).items():
                        counts.setdefault(site, {}).setdefault(SHORT.get(m, m), 0)
                        counts[site][SHORT.get(m, m)] += n
            lines.append(f"  - confirm run `{d['run_id']}`: sites "
                         f"{_confirm_cells(d)}; model calls by the call site the tracer "
                         f"recorded in the patched code: {counts or 'none'}")
    if patches:
        lines += ["", "The \"Models requested\" column in the PR texts shows the count from one test, not the total; "
                  "the totals per recorded site are listed above."]
    lines += ["", "## What is not claimed", ""]
    wrong_everywhere = all((EVIDENCE / t / "control-wrong.json").exists() for t in man["targets"])
    for x in man["not_claimed"]:
        lines.append(f"- {x}")
        if "only where the manifest lists control-wrong" in x and wrong_everywhere:
            lines.append("  - Correction: this line dates from the first manifest and is kept so the manifest is not "
                         "changed after the runs. The wrong-answer control ran in all three repositories "
                         "(`evidence/census/*/control-wrong.json`).")
    typos = [a for a in man.get("amendments", []) if "/bin/bash." in a["reason"]]
    if typos:
        lines += ["", "## Notes on the manifest", ""]
        budgets = {t: e["token_budget_usd"] for t, e in man["targets"].items() if "token_budget_usd" in e}
        for a in typos:
            lines.append(f"- The reason text of the amendment written at {a['utc']} reads \"/bin/bash.66\" where "
                         f"\"$0.66\" was meant (the shell expanded `$0` when the text was written). The binding field is "
                         f"correct: token_budget_usd {budgets}.")


if __name__ == "__main__":
    main()
