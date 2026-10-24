"""Write CENSUS.md from the evidence: every number comes from census/MANIFEST.json, the ladder files
(evidence/census/<target>/ladder-*.json), coverage.json and, when present, the patch results. Run again to recompute.

    .venv/bin/python census/report.py            # writes CENSUS.md at the repository root
"""
import json, math, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_moving_day import ladder, pr  # noqa: E402

EVIDENCE = ROOT / "evidence" / "census"
RUNG_ORDER = [r for r, _ in ladder.RUNGS]
SHORT = {m: r for r, m in ladder.RUNGS}

# What each measured call site does (read from the target's source at the pinned commit).
ROLES = {
    "instructor/v2/core/retry.py:224": "instructor's sync retry funnel: every structured-output call (tool calls, JSON) of a sync client",
    "instructor/v2/core/retry.py:523": "the async twin of the same funnel",
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
    lines = ["# Model Moving Day: census of three open-source LLM apps", "",
             "Every number below is recomputed by `census/report.py` from the evidence in this repository "
             "(`evidence/census/<repo>/ladder-*.json`, `coverage.json`, `census/MANIFEST.json`). Test traffic, not "
             "production traffic.", ""]
    all_sites, decisions, infra_total, token_total, diag_total, smoke_lines = [], {}, 0, 0.0, 0.0, []
    per_target = []
    for target in man["targets"]:
        dec = [d for d, _ in runs(target, "decision") if d["status"] == "complete"]
        smoke = runs(target, "smoke")
        for d, p in smoke:
            smoke_lines.append(f"- `{target}` smoke `{d['run_id']}` ({d['smoke']}, K={d['k']}, rungs {', '.join(d['rungs'])}): "
                               f"{d['status']}; **not for decision**")
        if not dec:
            per_target.append((target, None))
            continue
        d = dec[-1]
        per_target.append((target, d))
        for j in d["jobs"]:
            token_total += j.get("model_spend") or 0
            diag_total += j.get("sandbox_cost_diagnostic") or 0
        for s, v in d["sites"].items():
            all_sites.append((target, s, v))
            infra_total += sum(c.get("infra_runs", 0) for c in v["cells"])
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
    lines += ["## Headline", "",
              f"- Call sites measured: {len(all_sites)} in {sum(1 for _, d in per_target if d)} repositories; "
              f"moved to Lightning or Nano: {len(sites_small)} of {len(all_sites)}.",
              f"- Independent decisions (one funnel's sync and async faces, or sites reached by the same tests, count "
              f"once; a group counts at its largest chosen model): {len(measured)}; on Lightning or Nano: {len(small)} "
              f"(Wilson 95 % [{lo:.2f}-{hi:.2f}]).",
              f"- Runs set aside as infrastructure (rate limits, 503, lost runs) and re-run: {infra_total}. "
              f"Sites with no decision because of infrastructure: "
              f"{sum(1 for _, _, v in all_sites if v['status'] == 'infra')}.",
              f"- Coverage gate (at least 3 repositories and 5 independent sites): "
              f"{'met' if sum(1 for _, d in per_target if d) >= 3 and len(measured) >= 5 else 'NOT met'} "
              f"({sum(1 for _, d in per_target if d)} repositories, {len(measured)} independent decisions).",
              f"- Model spend for the decision ladders: {money(token_total)} in tokens. The Sandboxes `cost` field summed "
              f"to {money(diag_total, 2)}; it is diagnostic (the 2026-10-24 billing check found no Sandboxes charge).", ""]
    lines += ["## Which model does what", "",
              f"| Repository | Call site | What the call does | Model after the census | $/1k calls after | "
              f"$/1k calls before ({base['model']}) | $ per passed test run after / before | Calls per test run |",
              "|---|---|---|---|---|---|---|---|"]
    for target, s, v in all_sites:
        c = chosen_cell(v)
        after = money(c["usd_per_1000_calls"], 3) if c else "-"
        b = pr.baseline_usd_per_1000(c, price) if c else None
        pb = pr.baseline_usd_per_passed_run(c, price) if c else None
        model = (f"**{v['chosen']}**" + (f" + {v['fix']['kind']}" if v.get("fix") else "")) if v["status"] == "chosen" \
            else ("old model stays" if v["status"] == "red" else v["status"])
        lines.append(f"| {target} | `{s}` | {ROLES.get(s, '-')} | {model} | {after} | {money(b, 3)} | "
                     f"{money(c.get('usd_per_passed_run') if c else None, 5)} / {money(pb, 5)} | "
                     f"{c.get('calls_per_test_run') if c else '-'} |")
    lines += ["", f"\"Before\" prices the same measured calls (prompt tokens and visible output tokens) at {base['model']} "
              f"(${price[0]}/${price[1]} per 1M tokens), the replacement the vendor recommends for the models these tests "
              f"name ({base['source']}). Diagnosis and fixes: the brain (Super or Ultra by repo size) is asked only for a "
              f"red cell whose call log shows a diagnosis; the embedding calls of paper-qa go to "
              f"{man['rules']['embedding_model']['model']} on every rung.", ""]
    lines += ["## Every cell", ""]
    for target, d in per_target:
        if not d:
            lines += [f"### {target}", "", "No complete decision run.", ""]
            continue
        m = d["manifest"]
        lines += [f"### {target}", "",
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
        lines += [""]
        for s, v in d["sites"].items():
            if v.get("fix"):
                f = v["fix"]
                lines += [f"Fix at `{s}`: **{f['kind']}**, proposed by `{f['by']}` from the call log "
                          f"(\"{f['why']}\"); the request change showed on {f.get('applied_calls')} calls.", ""]
    lines += ["## Found, not measured", ""]
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
        lines += [f"- {t}: ladder run `{r['ladder_run']}`; moved {len(r['moved'])} sites, kept {len(r['kept'])}; "
                  f"confirm {r['confirm']}; local branch `{r['branch']}` commit `{(r['commit'] or '-')[:12]}`; "
                  f"PR text `evidence/census/{t}/patch/PR.md`"]
    lines += ["", "## What is not claimed", ""] + [f"- {x}" for x in man["not_claimed"]]
    (ROOT / "CENSUS.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"sites": len(all_sites), "independent": len(measured), "small": len(small),
                      "tokens": round(token_total, 4), "diag": round(diag_total, 2)}))


if __name__ == "__main__":
    main()
