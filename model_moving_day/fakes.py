"""Fake model and fake sandbox for the dry run: the whole census path (ladder, diagnosis, plan, PR text) runs
without calling Token Factory or Sandboxes. Outcomes are scripted per (call site, rung, params) below.

    python -m model_moving_day.fakes        # writes evidence/census/_dryrun/{ladder.json,PR.md}
"""
import json
from pathlib import Path

from model_moving_day import tf_client as tf

# site -> rung -> (passes without fix, passes with reasoning off, diagnoses recorded by the proxy)
SCRIPT = {
    "app/extract.py:40": {"Nano": (False, True, {"empty_content_length": 4})},
    "app/summary.py:12": {"Nano": (False, False, {}), "Lightning": (True, True, {})},
    "app/plan.py:88": {"Nano": (False, False, {}), "Lightning": (False, False, {}), "Super": (False, False, {}),
                       "Ultra": (False, False, {})},
}
FLIPS = {"app/extract.py:40": ["tests/test_extract.py::test_names"], "app/summary.py:12": ["tests/test_summary.py::test_len"],
         "app/plan.py:88": ["tests/test_plan.py::test_steps"]}
RUNG_OF = {"nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": "Nano", "nvidia/Nemotron-3_5-Lightning": "Lightning",
           "nvidia/nemotron-3-super-120b-a12b": "Super", "nvidia/Nemotron-3-Ultra-550b-a55b": "Ultra"}


class FakeBox:
    tag = "c1"


def fake_run_job(box, image, t, routes, k, cap_usd, ledger, **kw):
    """Simulates the in-sandbox job: per test, pass/fail from SCRIPT for the routed rung and params."""
    tests = [a for a in t.pytest_args if "::" in a]
    runs = [{} for _ in range(k)]
    sites = {}
    for site, flip in FLIPS.items():
        route = dict(routes["default"], **routes.get("sites", {}).get(site, {}))
        rung = RUNG_OF[route["model"]]
        ok_plain, ok_fixed, diag = SCRIPT[site].get(rung, (True, True, {}))
        ok = ok_fixed if route.get("reasoning") == "off" else ok_plain
        if not any(x in tests for x in flip):
            continue
        for i in range(k):
            for x in flip:
                runs[i][x] = {"outcome": "passed" if ok else "failed", "message": "" if ok else "AssertionError: scripted"}
        sites[site] = {"calls": 2 * k, "prompt_tokens": 1500 * k, "completion_tokens": 300 * k,
                       "reasoning_tokens": 0 if route.get("reasoning") == "off" else 200 * k, "cost": 0.0,
                       "diagnoses": {} if route.get("reasoning") == "off" else diag, "finish": {"stop": 2 * k},
                       "max_tokens": {}, "changes": {"dropped:seed": 2 * k}}
    summary = {"runs": runs, "calls": {"tests": {}, "sites": sites}, "envelope_spent": 0.0, "durations_s": [0.0] * k}

    class R:
        cost, elapsed_s, wall_s = 0.0, 0.0, 0.0
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


def dry_run(out_dir):
    """Run census.cmd_ladder and pr.render on the scripted target; nothing leaves this process."""
    import model_moving_day.census as census
    from model_moving_day import agent, pr, runner
    out_dir = Path(out_dir)
    (out_dir / "_dryrun").mkdir(parents=True, exist_ok=True)
    targets = out_dir / "_dryrun" / "targets.json"
    targets.write_text(json.dumps({"_dryrun": {"github": "example/app", "commit": "0" * 40, "install": [],
                                               "pytest_args": [], "node_args": ["-q"], "license": "MIT"}}))
    cov = {"traced_sites": [{"site": s, "flip_tests": f, "covered": True} for s, f in FLIPS.items()],
           "ast_sites_not_reached": ["app/unused.py:7"]}
    (out_dir / "_dryrun" / "coverage.json").write_text(json.dumps(cov))
    (out_dir / "_dryrun" / "image.json").write_text(json.dumps({"image_uuid": "fake"}))
    saved = (census.TARGETS, census.EVIDENCE, runner.run_job, census.sb.Sandbox, census.tf.Ledger, census.tf.Client,
             agent.pick_brain, census.ast_sites)
    try:
        census.TARGETS, census.EVIDENCE = targets, out_dir
        runner.run_job = fake_run_job
        census.sb.Sandbox = lambda *a, **k: FakeBox()
        census.tf.Ledger = lambda *a, **k: None
        census.tf.Client = lambda *a, **k: FakeBrainClient()
        agent.pick_brain = lambda repo: agent.SUPER
        census.ast_sites = lambda name: type("S", (), {"sites": []})()
        lo = census.cmd_ladder("_dryrun", k=3, margin=0.2, cap=0.0)
    finally:
        (census.TARGETS, census.EVIDENCE, runner.run_job, census.sb.Sandbox, census.tf.Ledger, census.tf.Client,
         agent.pick_brain, census.ast_sites) = saved
    text = pr.render({"name": "_dryrun", "github": "example/app", "commit": "0" * 40, "license": "MIT"}, lo, cov,
                     {"model": "gpt-6-luna", "price": (0.10, 0.50), "retiring_model": "gpt-4.1-mini",
                      "source": "dry run, illustrative prices"}, "dry-run")
    (out_dir / "_dryrun" / "PR.md").write_text(text)
    return lo, text


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "evidence" / "census"
    lo, _ = dry_run(root)
    print(json.dumps({s: (v["chosen"], v["reason"]) for s, v in lo["sites"].items()}))
