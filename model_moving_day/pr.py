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
    if c.get("status") == "infra":
        return f"infra: {c['infra_runs']} runs lost ({c['passed_runs']}/{c['valid_runs']} valid)"
    mark = "✅" if c["passes"] else "❌"
    fix = ""
    if c.get("fix"):
        fix = f" +{c['fix']['kind']}" + ("" if c["fix"].get("applied", True) else " (not applied)")
    lo, hi = c["wilson95"]
    infra = f", {c['infra_runs']} infra" if c.get("infra_runs") else ""
    return f"{mark} {c['passed_runs']}/{c['valid_runs']}{fix} [{lo:.2f}-{hi:.2f}]{infra}"


def _visible_out(cell):
    tout = cell["tokens_per_call"][1]
    return max(0.0, tout - cell["reasoning_tokens"] / cell["calls"])


def baseline_usd_per_1000(cell, price):
    """Vendor replacement priced on the tokens we measured: same prompt, visible output only (no reasoning)."""
    tin = cell["tokens_per_call"][0]
    if tin is None or not cell["calls"]:
        return None
    return 1000 * (tin * price[0] + _visible_out(cell) * price[1]) / 1e6


def baseline_usd_per_passed_run(cell, price):
    """The same calls the cell made per passed run, priced at the vendor replacement."""
    per_1000 = baseline_usd_per_1000(cell, price)
    if per_1000 is None or not cell["passed_runs"]:
        return None
    return per_1000 / 1000 * cell["calls"] / cell["passed_runs"]


def _outcome(v):
    if v["status"] == "chosen":
        return v["chosen"] + (f" (+{v['fix']['kind']})" if v.get("fix") else "")
    if v["status"] == "infra":
        return f"infra: not measured ({v['reason']})"
    if v["status"] == "red":
        return "**stays on the old model**"
    return f"{v['status']}: {v['reason']}"


def _fallback(v):
    fb = v.get("fallback")
    if v["status"] != "chosen":
        return "-"
    if v["chosen"] == "Ultra":
        return "none above Ultra (the error is raised)"
    if not fb:
        return "not measured (the error is raised)"
    return f"{fb['rung']} ✅ measured" if fb["passes"] else f"none: {fb['rung']} did not pass (the error is raised)"


def render(target, ladder_out, coverage, baseline, manifest_sha=None, changes_by_site=None, edits=None, confirm=None,
           approved_by=None, manifest=None, dry_run=False):
    """target: dict(name, github, commit, license); baseline: dict(model, price, source, retiring_model).
    confirm: {site: confirm cell record} from the run of the patched app; approved_by: who approved the plan."""
    sites = ladder_out["sites"]
    excluded = ladder_out.get("excluded_sites") or coverage.get("excluded_sites") or {}
    measured = [s for s, v in sites.items() if v["status"] in ("chosen", "red")]
    moved = [s for s, v in sites.items() if v["status"] == "chosen"]
    small = [s for s, v in sites.items() if v["status"] == "chosen" and v["chosen"] in ("Nano", "Lightning")]
    infra = [s for s, v in sites.items() if v["status"] == "infra"]
    m = ladder_out.get("manifest") or {}
    sha = manifest_sha or m.get("sha256", "-")
    title = f"# Move {target['name']}'s LLM calls to NVIDIA Nemotron on Nebius Token Factory"
    lines = ([f"> DRY RUN on a scripted fake model: every number below is illustrative, nothing was measured.", ""]
             if dry_run else []) + [("# DRY RUN: " + title[2:]) if dry_run else title, "",
             f"Why: `{baseline['retiring_model']}` is on the vendor's retirement list; the vendor's recommended "
             f"replacement is `{baseline['model']}`. This change measures, call site by call site, the smallest Nemotron "
             f"model whose runs pass this repository's own tests (frozen before the run) at least as often as the rule "
             f"below requires.", "",
             f"- Repository: `{target['github']}` at `{target['commit'][:12]}` ({target['license']})",
             f"- Call sites reached by the repo's own tests and sensitive to the answer (must-differ control): "
             f"{len(sites)}; measured to a decision: {len(measured)}; moved: {len(moved)}; moved to Lightning or Nano: "
             f"{len(small)}; lost to infrastructure errors: {len(infra)}",
             f"- Ladder: {' -> '.join(ladder_out['rungs'])}, K={ladder_out['k']} runs per cell ({ladder_out.get('kind', 'decision')} run "
             f"`{ladder_out.get('run_id', '-')}`); a cell passes when at least K runs are valid and its pass rate is at "
             f"least the baseline's minus {ladder_out['margin']:.2f}. Baseline: {ladder_out.get('baseline_note', ladder_out.get('baseline'))}",
             f"- Manifest SHA-256 `{sha}`" + (f", pushed {m['pushed_at']}" if m.get("pushed_at") else "") +
             (f" (commit `{m['commit'][:12]}` on `{m.get('branch', '')}`)" if m.get("commit") else ""), "",
             "## Per call site", "",
             "| Call site | Tests (flip) | " + " | ".join(RUNG_ORDER) + " | Outcome | Fallback | $/1k calls after | "
             f"$/1k calls before ({baseline['model']}) | $ per passed run after / before | Request changes |",
             "|---|---|" + "---|" * len(RUNG_ORDER) + "---|---|---|---|---|---|"]
    for s, v in sites.items():
        by_rung = {}
        for c in v["cells"]:
            if c.get("role", "ladder") == "ladder":
                by_rung.setdefault(c["rung"], []).append(c)
        cells = [" / ".join(_cell_text(c) for c in by_rung.get(r, [])) or "-" for r in RUNG_ORDER]
        chosen = next((c for c in v["cells"] if c["passes"] and c.get("role", "ladder") == "ladder"), None)
        after = f"{chosen['usd_per_1000_calls']:.3f}" if chosen and chosen["usd_per_1000_calls"] is not None else "-"
        b = baseline_usd_per_1000(chosen, baseline["price"]) if chosen else None
        before = f"{b:.3f}" if b is not None else "-"
        pa = chosen.get("usd_per_passed_run") if chosen else None
        pb = baseline_usd_per_passed_run(chosen, baseline["price"]) if chosen else None
        per_run = f"{pa:.5f} / {pb:.5f}" if pa is not None and pb is not None else "-"
        ch = ", ".join(sorted((changes_by_site or {}).get(s, []))) or "model"
        if (v.get("flags") or {}).get("cassette_only"):
            ch += "; good side = recorded cassette: live forward did not pass before the ladder"
        lines.append(f"| `{s}` | {len(v['flip_tests'])} | " + " | ".join(cells) +
                     f" | {_outcome(v)} | {_fallback(v)} | {after} | {before} | {per_run} | {ch} |")
    for s, why in excluded.items():
        lines.append(f"| `{s}` | - | " + " | ".join(["-"] * len(RUNG_ORDER)) +
                     f" | not measured: excluded before the run ({why}) | - | - | - | - | - |")
    lines += ["", "Cell = passed runs / valid runs, Wilson 95 % interval in brackets; `+fix` marks a re-run after the "
              "agent's request fix; `infra` runs were lost to the sandbox, the network or the platform and are not counted.",
              "", "## Receipts (every cell, including failures, infrastructure losses and fallback cells)", "",
              "| Call site | Role | Rung | Params | Status | Passed / valid (infra) | $/1k calls | $ per passed run | "
              "Calls per test run | Reasoning tokens | Diagnoses | First failure |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s, v in sites.items():
        for c in v["cells"]:
            usd = "-" if c["usd_per_1000_calls"] is None else f"{c['usd_per_1000_calls']:.3f}"
            ppr = "-" if c.get("usd_per_passed_run") is None else f"{c['usd_per_passed_run']:.5f}"
            lines.append(f"| `{s}` | {c.get('role', 'ladder')} | {c['rung']} | {c['params'] or '-'} | {c.get('status', '-')} | "
                         f"{c['passed_runs']}/{c['valid_runs']} ({c.get('infra_runs', 0)}) | {usd} | {ppr} | "
                         f"{c.get('calls_per_test_run', '-')} | {c['reasoning_tokens']} | {c['diagnoses'] or '-'} | "
                         f"{(c['first_failure'] or '-')[:160].replace('|', '/')} |")
    if confirm:
        lines += ["", "## Confirm run of the patched app (route passthrough: the body exactly as the app built it)", "",
                  "| Call site | Result | Models the helper sent |", "|---|---|---|"]
        for s, c in confirm.items():
            lines.append(f"| `{s}` | {_cell_text(c)} | {c.get('models') or '-'} |")
    red = [s for s, v in sites.items() if v["status"] == "red"]
    traced_uncovered = [r["site"] for r in coverage.get("traced_sites", []) if not r["covered"] and not r.get("excluded")]
    not_reached = [x for x in coverage.get("ast_sites_not_reached", []) if x not in excluded]
    lines += ["", "## What is not proven", "",
              f"- Call sites where no Nemotron model passed (the old model stays): {', '.join(f'`{s}`' for s in red) or 'none'}",
              f"- Call sites lost to infrastructure errors (no decision): {', '.join(f'`{s}`' for s in infra) or 'none'}",
              f"- Excluded before the run, with the reason: "
              f"{'; '.join(f'`{s}` ({why})' for s, why in excluded.items()) or 'none'}",
              f"- Reached by the tests, but no test fails on an empty answer there (not moved, not measured): "
              f"{', '.join(f'`{s}`' for s in traced_uncovered) or 'none'}",
              f"- Found in the source but reached by none of the kept tests (not moved, not measured): "
              f"{', '.join(f'`{s}`' for s in not_reached) or 'none'}",
              f"- Baseline cost is priced from token counts measured on Nemotron (prompt tokens and visible output tokens); "
              f"another tokenizer differs, so treat it as an estimate. Prices: {baseline['source']}.",
              f"- K={ladder_out['k']} runs per cell is a small sample; the intervals above say how small."]
    lines += [f"- {x}" for x in (manifest or {}).get("not_claimed", [])]
    lines += ["", "## How the move works", "",
              "Each moved call site goes through `nemotron_move` (added by this change). It is off unless "
              "`NEMOTRON_MOVE=1`; then the site sends the Nemotron model chosen above with the request parameters measured "
              "for it. `NEMOTRON_MODEL_<SITE>` overrides one site. A moved call made inside another moved call keeps the "
              "outer site's choice. When Token Factory answers that the model is unknown (it does not reroute retired "
              "models), the call retries once on the fallback in the table, only if that fallback was measured and passed; "
              "otherwise the error is raised. With `NEMOTRON_MOVE_BASE_URL` and `NEMOTRON_MOVE_API_KEY` set, the first "
              "moved call lists the platform's models and warns about any configured model that is no longer listed.", "",
              "## Reproduce", "", "```",
              f"python -m model_moving_day.census ladder {target['name']}" +
              (f" --smoke {ladder_out['smoke']}" if ladder_out.get("smoke") else ""), "```",
              "K, margin, rungs, flip tests and frozen hashes come from `census/MANIFEST.json`; the ladder refuses to run "
              "when the manifest does not match the code, the evidence or the clone.", "",
              "## Disclosure", "",
              "Written by the Model Moving Day agent (NVIDIA Nemotron on Nebius Token Factory). " +
              (f"{approved_by} approved the migration plan. " if approved_by else
               "No human has reviewed this change yet. ") +
              "The repository's tests, conftest files and pytest configuration (SHA-256 list in the manifest) were "
              "hash-checked inside the sandbox before and after every ladder job and the confirm run, and before and "
              "after every agent step while the patch was written. This branch is local and has not been proposed upstream."]
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
    """Census headline numbers over several targets: shares are over sites measured to a decision."""
    sites = [v for lo in ladder_outs for v in lo["sites"].values()]
    measured = [v for v in sites if v["status"] in ("chosen", "red")]
    small = sum(1 for v in measured if v["status"] == "chosen" and v["chosen"] in ("Nano", "Lightning"))
    return {"call_sites": len(sites), "measured": len(measured), "infra": sum(v["status"] == "infra" for v in sites),
            "moved": sum(1 for v in measured if v["status"] == "chosen"), "moved_to_lightning_or_nano": small,
            "share_small": small / len(measured) if measured else math.nan}
