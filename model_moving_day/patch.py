"""`census patch <target>`: turn the target's newest complete decision ladder into a local migration branch.

1. The manifest is verified as for the ladder, and the clone must sit at the pinned commit with no local changes.
2. Plan: every site the ladder chose, with its model and the request parameters the ladder measured for it,
   translated from proxy route terms into SDK keyword arguments (reasoning off -> extra_body.chat_template_kwargs,
   max_tokens floor, keys the proxy had to drop), plus the measured fallback when that cell passed.
3. The helper (templates/nemotron_move.py) is copied into the target package with SITES filled in.
4. The brain wraps each planned call site (agent.write_patch; frozen files are hash-checked before and after every
   step). Every planned site must end up wrapped, and the diff may touch only the planned files and the helper.
5. Confirm run of the patched app: NEMOTRON_MOVE=1, proxy route `passthrough` (the body exactly as the app built it),
   K runs of the moved sites' flip tests under the same infra rules; the calls must carry the planned models.
6. PR text rendered and committed with the patch to a local branch of the clone, which is then switched back to the
   pinned commit. Nothing is pushed.
"""
import json, pprint, subprocess
from pathlib import Path

from model_moving_day import find, freeze, ladder, manifest as mf, pr, runner, sandbox as sb, tf_client as tf

TEMPLATE = Path(__file__).resolve().parent / "templates" / "nemotron_move.py"
BRANCH = "mmd/move-to-nemotron"
PR_FILE = "NEMOTRON_MOVE_PR.md"
UNKNOWN_MODEL_EVIDENCE = mf.ROOT / "evidence" / "platform" / "unknown-model.json"


class PatchError(RuntimeError):
    pass


def sdk_params(cell, model):
    """What a patched call must send to reproduce the request the ladder measured in `cell` for `model`."""
    route, changes = cell.get("params") or {}, cell.get("changes") or {}
    ctk, drop, notes = {}, set(), []
    if route.get("reasoning") == "off":
        ctk["enable_thinking"] = False
    if "reasoning:reasoning_effort" in changes:  # the app asked for effort "none": the template switch works instead
        drop.add("reasoning_effort")
        ctk["enable_thinking"] = False
    if model == ladder.ULTRA and "reasoning:chat_template_kwargs" in changes:
        ctk.update(enable_thinking=True, force_nonempty_content=True)
    for key in changes:
        kind, _, name = key.partition(":")
        if kind == "dropped" and name != "prefill":
            drop.add(name)
    if "dropped:prefill" in changes:
        notes.append("the proxy dropped an assistant prefill during the ladder; the helper cannot; see the confirm run")
    return {"model": model, "params": {"extra_body": {"chat_template_kwargs": ctk}} if ctk else {},
            "drop": sorted(drop), "min_max_tokens": route.get("min_max_tokens"), "notes": notes}


def _slug(site):
    file, _, line = site.rpartition(":")
    return f"{Path(file).stem}_{line}"


def plan(doc, clone):
    """Planned sites from a decision ladder run: slug, site, sync/async, the line of code, model and SDK config."""
    sites = []
    for site, v in doc["sites"].items():
        if v["status"] != "chosen":
            continue
        chosen = next(c for c in v["cells"] if c.get("role", "ladder") == "ladder" and c["passes"])
        fb_cell = next((c for c in v["cells"] if c.get("role") == "fallback"), None)
        cfg = sdk_params(chosen, v["model"])
        cfg["fallback"] = ({k: x for k, x in sdk_params(fb_cell, fb_cell["model"]).items() if k != "notes"}
                           if fb_cell and fb_cell["passes"] else None)
        file, _, line = site.rpartition(":")
        code = (Path(clone) / file).read_text().splitlines()[int(line) - 1].strip()
        sites.append({"slug": _slug(site), "site": site, "is_async": code.startswith("await ") or " await " in code,
                      "code": code, "model": v["model"], "fallback": (cfg["fallback"] or {}).get("model"), "config": cfg})
    return {"sites": sites}


def retired_statuses():
    """Token Factory's measured answer to an unknown model ID (evidence written in stage 2), else 404."""
    if UNKNOWN_MODEL_EVIDENCE.exists():
        return sorted(set(json.loads(UNKNOWN_MODEL_EVIDENCE.read_text())["statuses"]))
    return [404]


def render_helper(the_plan, statuses):
    src = TEMPLATE.read_text()
    sites = {s["slug"]: {k: v for k, v in s["config"].items() if k != "notes"} for s in the_plan["sites"]}
    for old, new in (("SITES = {}  # filled in by the migration patch",
                      "SITES = " + pprint.pformat(sites, sort_dicts=True, width=110)),
                     ("RETIRED_STATUSES = {404}  # filled in by the migration patch from Token Factory's measured answer to an unknown ID",
                      f"RETIRED_STATUSES = {set(statuses)!r}  # Token Factory's answer to an unknown model ID")):
        if src.count(old) != 1:
            raise PatchError(f"helper template changed: cannot fill {old[:40]!r}")
        src = src.replace(old, new)
    compile(src, "nemotron_move.py", "exec")
    return src


def _git(clone, *args):
    return subprocess.run(["git", *args], cwd=clone, capture_output=True, text=True, check=True).stdout


def changed_paths(clone):
    return sorted(line[3:].strip() for line in _git(clone, "status", "--porcelain", "--untracked-files=all").splitlines())


def check_wrapped(clone, the_plan, helper_rel):
    """Planned sites the patch did not wrap, and paths the diff touched outside the plan."""
    files = {s["site"].rpartition(":")[0] for s in the_plan["sites"]}
    missing = [s["slug"] for s in the_plan["sites"]
               if f'"{s["slug"]}"' not in (Path(clone) / s["site"].rpartition(":")[0]).read_text()]
    stray = [p for p in changed_paths(clone) if p not in files | {helper_rel}]
    return missing, stray


def write_and_check(brain, clone, frozen, the_plan, helper_import, helper_rel):
    edits = brain.write_patch(clone, frozen, the_plan, helper_import)
    missing, stray = check_wrapped(clone, the_plan, helper_rel)
    if missing and not stray:
        feedback = "Not wrapped yet: " + ", ".join(missing) + ". Wrap them, then finish."
        edits += brain.write_patch(clone, frozen, {"sites": [s for s in the_plan["sites"] if s["slug"] in missing]},
                                   helper_import, feedback=feedback)
        missing, stray = check_wrapped(clone, the_plan, helper_rel)
    if missing or stray:
        raise PatchError(f"patch incomplete: not wrapped {missing}, outside the plan {stray}")
    freeze.verify(clone, frozen)
    return edits


def confirm(run, the_plan, clone, changed, port=8765):
    """K runs of the moved sites' flip tests on the patched app; returns {site: confirm cell record}."""
    files = {rel: str(Path(clone) / rel) for rel in changed}
    env = {"NEMOTRON_MOVE": "1", "NEMOTRON_MOVE_BASE_URL": f"http://127.0.0.1:{port}/v1", "NEMOTRON_MOVE_API_KEY": "mmd-proxy"}
    routes = {"default": {"mode": "passthrough"}, "spans": run.spans}
    by_site = {s["site"]: s for s in the_plan["sites"]}
    res = run.measure("confirm", None, list(by_site), {}, "confirm", routes=routes, env=env, files=files)
    out = {}
    for site, (outs, stats) in res.items():
        rec = run.record(site, "confirm", by_site[site]["model"], outs, stats, role="confirm")
        allowed = {by_site[site]["model"], by_site[site]["fallback"]} - {None}
        rec["models"] = stats.get("models", {})
        rec["helper_sent_other_models"] = sorted(set(rec["models"]) - allowed)
        if rec["helper_sent_other_models"] and rec["passes"]:
            rec.update(passes=False, status="helper sent another model")
        out[site] = rec
    run.checkpoint()
    return out


def cmd_patch(name, clones=None, brain=None, run_job=None, box=None, ledger=None, check_pushed=True):
    from model_moving_day import agent, census
    clones = Path(clones or census.CLONES)
    man, sha = mf.load()
    problems = mf.verify(man, name, clones)
    if problems:
        raise mf.ManifestMismatch(problems)
    push = mf.pushed() if check_pushed else {}
    entry, targets = man["targets"][name], json.loads(mf.TARGETS.read_text())[name]
    doc, doc_path = census.latest_ladder(name)
    if doc["manifest"].get("sha256") != sha:
        raise PatchError(f"the ladder run {doc['run_id']} used manifest {doc['manifest'].get('sha256')}, not {sha}")
    clone = clones / name
    the_plan = plan(doc, clone)
    out = census.out_dir(name) / "patch"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps({"ladder_run": doc["run_id"], **the_plan}, indent=1))
    pkg = entry["helper_package"]
    helper_rel, helper_import = f"{pkg}/nemotron_move.py", f"{pkg.replace('/', '.')}.nemotron_move"
    frozen = freeze.Frozen(entry["frozen"]["commit"], sorted({t for ts in entry["flip_tests"].values() for t in ts}),
                           entry["frozen"]["hashes"], entry["frozen"]["extra_paths"])
    original = _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip()
    edits, conf = [], {}
    if the_plan["sites"]:
        (clone / helper_rel).write_text(render_helper(the_plan, retired_statuses()))
        brain = brain or agent.Brain(tf.Client("c1"), agent.pick_brain(clone), out / "agent-steps.jsonl")
        edits = write_and_check(brain, clone, frozen, the_plan, helper_import, helper_rel)
        changed = changed_paths(clone)
        (out / "patch.diff").write_text(_git(clone, "diff") + "".join(
            f"\n--- new file {p}\n" + (clone / p).read_text() for p in changed if p == helper_rel))
        run = census.Ladder(name, entry, man["rules"], man["rules"]["k"], [], "confirm", census.out_dir(name),
                            run_job or runner.run_job, box=box or sb.Sandbox(), ledger=ledger or tf.Ledger(),
                            manifest={"sha256": sha, **push}, cap=man["budgets"]["cap_usd_per_job"],
                            spans=find.site_spans(census.ast_sites(name).sites))
        run.event("start", kind="confirm", ladder_run=doc["run_id"])
        try:
            conf = confirm(run, the_plan, clone, changed)
            run.doc["status"] = "complete"
        except BaseException as e:
            run.doc.update(status="aborted", error=f"{type(e).__name__}: {e}"[:800])
            raise
        finally:
            run.doc["ended_utc"] = census._utc()
            run.event("end", status=run.doc["status"])
            run.checkpoint()
    cov = json.loads((census.out_dir(name) / "coverage.json").read_text())
    base = man["baseline_before"]
    text = pr.render({"name": name, "github": entry["github"], "commit": entry["commit"], "license": entry["license"]},
                     doc, cov, {"model": base["model"], "price": tuple(base["usd_per_1m_tokens"]),
                                "source": base["source"], "retiring_model": targets.get("retiring_model", "-")},
                     edits=edits, confirm=conf, manifest=man)
    (out / "PR.md").write_text(text)
    if not the_plan["sites"]:
        return {"sites": 0, "pr": str(out / "PR.md")}
    (clone / PR_FILE).write_text(text)
    sha_commit = pr.commit_to_local_branch(clone, BRANCH, changed_paths(clone), f"Move {len(the_plan['sites'])} LLM call "
                                           f"sites to NVIDIA Nemotron on Token Factory (opt-in: NEMOTRON_MOVE=1)", PR_FILE)
    _git(clone, "checkout", "-q", original)  # the clone goes back to the pinned commit; the patch stays on BRANCH
    result = {"sites": len(the_plan["sites"]), "branch": BRANCH, "commit": sha_commit, "pr": str(out / "PR.md"),
              "confirm": {s: c["status"] for s, c in conf.items()}}
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result))
    return result
