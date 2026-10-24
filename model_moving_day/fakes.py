"""Fake model and fake sandbox for the dry run: the whole census path (ladder, diagnosis, plan, PR text) runs
without calling Token Factory or Sandboxes. Outcomes are scripted per (call site, rung, params) below.

    python -m model_moving_day.fakes        # writes evidence/census/_dryrun/{ladder.json,PR.md}
"""
import json
from pathlib import Path

from model_moving_day import tf_client as tf

# site -> rung -> (passes without fix, passes with reasoning off, diagnoses recorded by the proxy, infra error status)
SCRIPT = {
    "app/extract.py:40": {"Nano": (False, True, {"empty_content_length": 4}, None)},
    "app/summary.py:12": {"Nano": (False, False, {}, None), "Lightning": (True, True, {}, None)},
    "app/plan.py:88": {"Nano": (False, False, {}, None), "Lightning": (False, False, {}, None),
                       "Super": (False, False, {}, None), "Ultra": (False, False, {}, None)},
    "app/flaky.py:7": {"Nano": (False, False, {}, "502")},  # the platform keeps failing: infra, never "Nano failed"
}
FLIPS = {"app/extract.py:40": ["tests/test_extract.py::test_names"], "app/summary.py:12": ["tests/test_summary.py::test_len"],
         "app/plan.py:88": ["tests/test_plan.py::test_steps"], "app/flaky.py:7": ["tests/test_flaky.py::test_it"]}
RUNG_OF = {"nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "Nano", "nvidia/Nemotron-3_5-Lightning": "Lightning",
           "nvidia/nemotron-3-super-120b-a12b": "Super", "nvidia/Nemotron-3-Ultra-550b-a55b": "Ultra"}


class FakeBox:
    tag = "c1"
    max_runs = None


def fake_run_job(box, image, t, routes, k, cap_usd, ledger, **kw):
    """Simulates the in-sandbox job: per test, pass/fail from SCRIPT for the routed rung and params."""
    tests = [a for a in t.pytest_args if "::" in a]
    runs = [{} for _ in range(k)]
    sites, reach = {}, {}
    for site, flip in FLIPS.items():
        if not any(x in tests for x in flip):
            continue
        route = dict(routes["default"], **routes.get("sites", {}).get(site, {}))
        rung = RUNG_OF[route["model"]]
        ok_plain, ok_fixed, diag, infra = SCRIPT[site].get(rung, (True, True, {}, None))
        off = route.get("reasoning") == "off"
        ok = ok_fixed if off else ok_plain
        for i in range(k):
            for x in flip:
                runs[i][x] = {"outcome": "passed" if ok else "failed", "message": "" if ok else "AssertionError: scripted"}
                reach[x] = [site]
        changes = {"model:model": 2 * k, **({"reasoning:enable_thinking": 2 * k} if off else {})}
        sites[site] = {"calls": 2 * k, "prompt_tokens": 1500 * k, "completion_tokens": 300 * k,
                       "reasoning_tokens": 0 if off else 200 * k, "cost": 0.0,
                       "diagnoses": {} if off else diag, "finish": {"stop": 2 * k}, "max_tokens": {}, "changes": changes,
                       "errors_by_run": {str(i): {infra: 1} for i in range(k)} if infra else {}}
    summary = {"runs": runs, "exit_codes": [0] * k, "calls": {"tests": reach, "sites": sites, "untraced": {}},
               "envelope_spent": 0.0, "durations_s": [0.0] * k, "freeze_before": [], "freeze_after": []}

    class R:
        cost, elapsed_s, wall_s, exit_code = 0.0, 0.0, 0.0, 0
    return R(), summary


class FakeBrainClient:
    """Answers every diagnosis with reasoning_off and a cause that cites the evidence."""

    def __init__(self):
        self.key = "fake"

    def chat(self, model, messages, **params):
        ev = json.loads(messages[-1]["content"])
        tc = [{"id": "1", "type": "function", "function": {"name": "propose_fix", "arguments": json.dumps(
            {"kind": "reasoning_off", "cause": f"diagnoses {ev.get('diagnoses')} at {ev.get('rung')}"})}}]
        return tf.Reply(model, "", "", "", False, tc, "tool_calls", {"prompt_tokens": 0, "completion_tokens": 0}, 0.0, 0.0, {})


def dry_run(out_dir, k=3):
    """Run the ladder engine and pr.render on the scripted target; nothing leaves this process."""
    from model_moving_day import agent, census, manifest as mf, pr
    out = Path(out_dir) / "_dryrun"
    out.mkdir(parents=True, exist_ok=True)
    for stale in ("agent-steps.jsonl", "targets.json", "image.json"):  # earlier dry runs; this one rewrites the rest
        (out / stale).unlink(missing_ok=True)
    entry = {"github": "example/app", "commit": "0" * 40, "install": [], "node_args": ["-q"], "env": {},
             "image": {"uuid": "fake", "tag": "fake"}, "flip_tests": FLIPS, "excluded_sites": {"app/responses.py:3": "example exclusion"},
             "frozen": {"sha256": "dry-run", "hashes": {"tests/test_extract.py": "0" * 64}, "extra_paths": []}}
    brain = agent.Brain(FakeBrainClient(), agent.SUPER, out / "agent-steps.jsonl", log=lambda m: None)
    run = census.Ladder("_dryrun", entry, mf.rules(), k, census.RUNGS, "decision", out, fake_run_job, brain=brain,
                        box=FakeBox(), manifest={"sha256": "dry-run"})
    lo = run.run()
    (out / "ladder.json").write_text(json.dumps(lo, indent=1))
    for p in out.glob("ladder-*.json"):  # keep the dry-run evidence to one stable file
        p.unlink()
    (out / "ladder-log.jsonl").unlink(missing_ok=True)
    cov = {"traced_sites": [{"site": s, "flip_tests": f, "covered": True} for s, f in FLIPS.items()],
           "ast_sites_not_reached": ["app/unused.py:7"], "excluded_sites": entry["excluded_sites"]}
    (out / "coverage.json").write_text(json.dumps(cov))
    text = pr.render({"name": "_dryrun", "github": "example/app", "commit": "0" * 40, "license": "MIT"}, lo, cov,
                     {"model": "gpt-6-luna", "price": (0.10, 0.50), "retiring_model": "gpt-4.1-mini",
                      "source": "dry run, illustrative prices"}, dry_run=True)
    (out / "PR.md").write_text(text)
    return lo, text


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "evidence" / "census"
    lo, _ = dry_run(root)
    print(json.dumps({s: (v["status"], v["chosen"], v["reason"]) for s, v in lo["sites"].items()}))
