"""Write the migration pull-request description: one row per call site, every ladder cell, receipts in $ per 1,000
calls against the vendor's recommended replacement, red cells and what is not proven. Nothing here touches GitHub:
the text goes into a commit on a local branch of the target clone.
"""
import math
import subprocess

RUNG_ORDER = ["Nano", "Lightning", "Super", "Ultra"]


def _cell_text(c):
    if not c:
        return "-"
    mark = "✅" if c["passes"] else "❌"
    fix = f" +{c['fix']['kind']}" if c.get("fix") else ""
    lo, hi = c["wilson95"]
    return f"{mark} {c['passed_runs']}/{c['valid_runs']}{fix} [{lo:.2f}-{hi:.2f}]"


def baseline_usd_per_1000(cell, price):
    """Vendor replacement priced on the tokens we measured: same prompt, visible output only (no reasoning)."""
    tin, tout = cell["tokens_per_call"]
    if tin is None or not cell["calls"]:
        return None
    visible_out = max(0.0, tout - cell["reasoning_tokens"] / cell["calls"])
    return 1000 * (tin * price[0] + visible_out * price[1]) / 1e6


def render(target, ladder_out, coverage, baseline, manifest_sha, changes_by_site=None, edits=None):
    """target: dict(name, github, commit, license); baseline: dict(model, price, source, retiring_model)."""
    sites = ladder_out["sites"]
    moved = [s for s, v in sites.items() if v["chosen"]]
    small = [s for s, v in sites.items() if v["chosen"] in ("Nano", "Lightning")]
    lines = [f"# Move {target['name']}'s LLM calls to NVIDIA Nemotron on Nebius Token Factory", "",
             f"Why: `{baseline['retiring_model']}` is on the vendor's retirement list; the vendor's recommended "
             f"replacement is `{baseline['model']}`. This change measures, call site by call site, the smallest Nemotron "
             f"model that still passes this repository's own tests, frozen before the run.", "",
             f"- Repository: `{target['github']}` at `{target['commit'][:12]}` ({target['license']})",
             f"- Call sites reached by the repo's own tests and sensitive to the answer (must-differ control): "
             f"{len(sites)}; moved: {len(moved)}; moved to Lightning or Nano: {len(small)}",
             f"- Call sites found but not covered by any test that checks the answer: "
             f"{len(coverage.get('ast_sites_not_reached', [])) + sum(1 for r in coverage.get('traced_sites', []) if not r['covered'])}",
             f"- Ladder: {' -> '.join(ladder_out['rungs'])}, K={ladder_out['k']} runs per cell; a cell passes when its pass "
             f"rate is at least the baseline's minus {ladder_out['margin']:.2f} (baseline: {ladder_out['baseline']})",
             f"- Manifest SHA-256 (committed before the run): `{manifest_sha}`", "",
             "## Per call site", "",
             "| Call site | Tests (flip) | " + " | ".join(RUNG_ORDER) + " | Chosen | Fallback | $/1k calls after | "
             f"$/1k calls before ({baseline['model']}) | Request changes |",
             "|---|---|" + "---|" * len(RUNG_ORDER) + "---|---|---|---|---|"]
    for s, v in sites.items():
        by_rung = {}
        for c in v["cells"]:
            by_rung.setdefault(c["rung"], []).append(c)
        cells = [" / ".join(_cell_text(c) for c in by_rung.get(r, [])) or "-" for r in RUNG_ORDER]
        chosen = next((c for c in v["cells"] if c["passes"]), None)
        after = f"{chosen['usd_per_1000_calls']:.3f}" if chosen and chosen["usd_per_1000_calls"] is not None else "-"
        b = baseline_usd_per_1000(chosen, baseline["price"]) if chosen else None
        before = f"{b:.3f}" if b is not None else "-"
        fb = RUNG_ORDER[RUNG_ORDER.index(v["chosen"]) + 1] if v["chosen"] and v["chosen"] != "Ultra" else "-"
        ch = ", ".join(sorted((changes_by_site or {}).get(s, []))) or "model"
        lines.append(f"| `{s}` | {len(v['flip_tests'])} | " + " | ".join(cells) +
                     f" | {v['chosen'] or '**stays on the old model**'} | {fb} | {after} | {before} | {ch} |")
    lines += ["", "Cell = passed runs / valid runs, Wilson 95 % interval in brackets; `+fix` marks a re-run after the "
              "agent's request fix.", "", "## Receipts per rung (every cell, including failures)", "",
              "| Call site | Rung | Params | Passed | $/1k calls | Calls | Reasoning tokens | Diagnoses | First failure |",
              "|---|---|---|---|---|---|---|---|---|"]
    for s, v in sites.items():
        for c in v["cells"]:
            usd = "-" if c["usd_per_1000_calls"] is None else f"{c['usd_per_1000_calls']:.3f}"
            lines.append(f"| `{s}` | {c['rung']} | {c['params'] or '-'} | {c['passed_runs']}/{c['valid_runs']} | {usd} | "
                         f"{c['calls']} | {c['reasoning_tokens']} | {c['diagnoses'] or '-'} | "
                         f"{(c['first_failure'] or '-')[:160].replace('|', '/')} |")
    red = [s for s, v in sites.items() if not v["chosen"]]
    uncovered = coverage.get("ast_sites_not_reached", []) + [r["site"] for r in coverage.get("traced_sites", []) if not r["covered"]]
    lines += ["", "## What is not proven", "",
              f"- Call sites where no Nemotron model passed (the old model stays): {', '.join(f'`{s}`' for s in red) or 'none'}",
              f"- Call sites with no test that checks the answer (not moved, not measured): "
              f"{', '.join(f'`{s}`' for s in uncovered) or 'none'}",
              f"- Baseline cost is priced from token counts measured on Nemotron (prompt tokens and visible output tokens); "
              f"OpenAI's tokenizer differs, so treat it as an estimate. Prices: {baseline['source']}.",
              f"- K={ladder_out['k']} runs per cell is a small sample; the intervals above say how small.", "",
              "## How the move works", "",
              "Each moved call site goes through `nemotron_move` (added by this change). It is off unless "
              "`NEMOTRON_MOVE=1`; then the site sends the Nemotron model chosen above with the measured request fixes. "
              "`NEMOTRON_MODEL_<SITE>` overrides one site. On HTTP 404 (Token Factory does not reroute retired models) "
              "the call retries once on the next rung up and logs it; on first use the helper lists Token Factory's "
              "/models and warns about configured models that are no longer listed.", "",
              "## Reproduce", "", "```", f"python -m model_moving_day.census ladder {target['name']} --k {ladder_out['k']} "
              f"--margin {ladder_out['margin']}", "```", "",
              "## Disclosure", "",
              "Written by the Model Moving Day agent (NVIDIA Nemotron on Nebius Token Factory). A human approved the "
              "migration plan; tests and fixtures were hash-checked before and after every agent step. This branch is "
              "local and has not been proposed upstream."]
    if edits:
        lines += ["", "## Edits", ""] + [f"- `{e['path']}`" for e in edits]
    return "\n".join(lines) + "\n"


def commit_to_local_branch(repo, branch, files, message, pr_text_path):
    """Create a local branch in the target clone and commit the patch plus the PR text. Never pushes."""
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    run("checkout", "-B", branch)
    run("add", "--", *files, pr_text_path)
    run("-c", "user.name=Model Moving Day", "-c", "user.email=mmd@localhost", "commit", "-q", "-m", message)
    return run("rev-parse", "HEAD")


def headline(ladder_outs, baseline_price):
    """Census headline numbers over several targets."""
    sites = [v for lo in ladder_outs for v in lo["sites"].values()]
    small = sum(1 for v in sites if v["chosen"] in ("Nano", "Lightning"))
    return {"call_sites": len(sites), "moved": sum(1 for v in sites if v["chosen"]),
            "moved_to_lightning_or_nano": small,
            "share_small": small / len(sites) if sites else math.nan}
