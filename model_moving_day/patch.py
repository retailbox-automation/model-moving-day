"""`census patch <target>`: turn a complete decision ladder run into a local migration branch of the target clone.

1. The manifest is verified as for the ladder, and the ladder run must have been made under this manifest or under
   an earlier version whose rules and target entry are the same. With more than one complete decision run the run
   must be named (--ladder-run); the PR lists every run of the target.
2. Work happens in a separate git worktree of the clone at the pinned commit, removed at the end whatever happens;
   the clone's own working tree is never touched. The patch branch stays in the clone's repository, never pushed.
3. Plan: every site the ladder chose, with its model, the request parameters the ladder measured, translated from
   proxy route terms into SDK keyword arguments, the call shape that was measured, and the measured fallback when
   that cell passed. When a moved site is a library funnel (a site the AST finder does not see), every chat call
   site of the target that was not chosen is wrapped as a guard ("keep"), so the funnel cannot move calls the
   ladder did not move.
4. The helper (templates/nemotron_move.py) is written into the target package with SITES filled in; the brain wraps
   each planned site (agent.write_patch; frozen files hash-checked before and after every step). Checks: every
   planned slug is the first argument of a nemotron_move call in its file; unwrapping those calls gives back the
   original syntax tree of every edited file (so with NEMOTRON_MOVE unset the app is the original); the diff touches
   only planned files and the helper, whose bytes equal the rendered helper.
5. Confirm run of the patched app: NEMOTRON_MOVE=1; the helper's endpoint is the proxy's /moved/ path (served as
   sent, `passthrough`), the app's own endpoint is the proxy's `old-model` route (logged, answered empty). K runs of
   the flip tests of moved and guarded sites. A moved site passes when its cell passes and every call of its flip
   tests went through /moved/ with the planned model (or its fallback); a guarded site's calls must not be moved.
6. Sites that do not pass the confirm run are turned into guards (not moved) and the helper is rendered again. The
   PR text and the patch are committed to a local branch only if at least one site is moved.
"""
import ast, json, pprint, re, subprocess, tempfile
from pathlib import Path

from model_moving_day import find, freeze, ladder, manifest as mf, pr, runner, sandbox as sb, tf_client as tf

TEMPLATE = Path(__file__).resolve().parent / "templates" / "nemotron_move.py"
BRANCH = "mmd/move-to-nemotron"
PR_FILE = "NEMOTRON_MOVE_PR.md"
UNKNOWN_MODEL_EVIDENCE = mf.ROOT / "evidence" / "platform" / "unknown-model.json"
CHAT_SHAPE = "openai.resources.chat.completions"
PROXY = "http://127.0.0.1:8765"


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


def slug(site):
    """A helper key from the whole relative path and line: unique across packages."""
    file, _, line = site.rpartition(":")
    return re.sub(r"[^A-Za-z0-9]+", "_", file).strip("_") + f"_{line}"


def _line(root, site):
    file, _, line = site.rpartition(":")
    return (Path(root) / file).read_text().splitlines()[int(line) - 1].strip()


def _entry(site, root, kind, model=None, fallback=None, config=None):
    code = _line(root, site)
    return {"slug": slug(site), "site": site, "is_async": code.startswith("await ") or " await " in code, "code": code,
            "kind": kind, "model": model or "keep (wrapped, not moved)", "fallback": fallback, "config": config}


def plan(doc, root, ast_sites):
    """Planned sites: chosen sites to move, plus guards when a moved site is a funnel the AST finder does not see.
    ast_sites: find.CallSite list of the clone at the pinned commit."""
    by_id = {s.id: s for s in ast_sites}
    out = []
    for site, v in doc["sites"].items():
        if v["status"] != "chosen":
            continue
        chosen = next(c for c in v["cells"] if c.get("role", "ladder") == "ladder" and c["passes"])
        fb_cell = next((c for c in v["cells"] if c.get("role") == "fallback"), None)
        cfg = sdk_params(chosen, v["model"])
        shape = "litellm" if by_id.get(site) and by_id[site].sdk == "litellm" else CHAT_SHAPE
        cfg.update(only=shape, fallback=({k: x for k, x in sdk_params(fb_cell, fb_cell["model"]).items() if k != "notes"}
                                         if fb_cell and fb_cell["passes"] else None))
        out.append(_entry(site, root, "move", v["model"], (cfg["fallback"] or {}).get("model"), cfg))
    moved = {e["site"] for e in out}
    if moved - set(by_id):  # a funnel moves everything that passes through it: guard the chat sites that stay
        for s in ast_sites:
            if s.id not in moved and s.kind == "chat" and s.api in ("chat.completions.create", "completion", "acompletion"):
                out.append(_entry(s.id, root, "keep", config={"keep": True}))
    slugs = [e["slug"] for e in out]
    if len(slugs) != len(set(slugs)):
        raise PatchError(f"helper keys collide: {sorted(x for x in slugs if slugs.count(x) > 1)}")
    return {"sites": out}


def retired_statuses():
    """Token Factory's measured answer to an unknown model ID (evidence/platform/unknown-model.json), else 404."""
    if UNKNOWN_MODEL_EVIDENCE.exists():
        return sorted(set(json.loads(UNKNOWN_MODEL_EVIDENCE.read_text())["statuses"])), "measured, evidence/platform/unknown-model.json"
    return [404], "assumed, not measured"


def render_helper(the_plan):
    src = TEMPLATE.read_text()
    statuses, how = retired_statuses()
    sites = {e["slug"]: {k: v for k, v in e["config"].items() if k != "notes"} for e in the_plan["sites"]}
    for old, new in (("SITES = {}  # filled in by the migration patch",
                      "SITES = " + pprint.pformat(sites, sort_dicts=True, width=110)),
                     ("RETIRED_STATUSES = {404}  # filled in by the migration patch",
                      f"RETIRED_STATUSES = {set(statuses)!r}  # Token Factory's answer to an unknown model ID: {how}")):
        if src.count(old) != 1:
            raise PatchError(f"helper template changed: cannot fill {old[:40]!r}")
        src = src.replace(old, new)
    compile(src, "nemotron_move.py", "exec")
    return src


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


def changed_paths(root):
    return sorted(line[3:].strip() for line in _git(root, "status", "--porcelain", "--untracked-files=all").splitlines())


class _Unwrap(ast.NodeTransformer):
    """nemotron_move.call(slug, F, *a, **kw) -> F(*a, **kw); the helper import is dropped. Collects the slugs."""

    def __init__(self):
        self.slugs = []

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("call", "acall") and isinstance(f.value, ast.Name) \
                and f.value.id == "nemotron_move" and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant):
            self.slugs.append(node.args[0].value)
            return ast.Call(func=node.args[1], args=node.args[2:], keywords=node.keywords)
        return node

    def visit_ImportFrom(self, node):
        names = [a for a in node.names if a.name != "nemotron_move"]
        return None if not names else ast.ImportFrom(module=node.module, names=names, level=node.level)

    def visit_Import(self, node):
        names = [a for a in node.names if not a.name.endswith(".nemotron_move")]
        return None if not names else ast.Import(names=names)


def check_patch(root, commit, the_plan, helper_rel, helper_src):
    """Problems with the patch in `root` (empty = fine)."""
    problems, files = [], {e["site"].rpartition(":")[0] for e in the_plan["sites"]}
    found = []
    for f in sorted(files):
        try:
            new = ast.parse((Path(root) / f).read_text())
        except SyntaxError as e:
            problems.append(f"{f} does not parse: {e}")
            continue
        un = _Unwrap()
        unwrapped = ast.fix_missing_locations(un.visit(new))
        found += un.slugs
        original = ast.parse(_git(root, "show", f"{commit}:{f}"))
        if ast.dump(unwrapped) != ast.dump(original):
            problems.append(f"{f}: besides wrapping planned calls the patch changed the code")
    missing = [e["slug"] for e in the_plan["sites"] if e["slug"] not in found]
    if missing:
        problems.append(f"not wrapped: {missing}")
    stray = [p for p in changed_paths(root) if p not in files | {helper_rel}]
    if stray:
        problems.append(f"outside the plan: {stray}")
    if (Path(root) / helper_rel).read_text() != helper_src:
        problems.append("the helper differs from the rendered one")
    return problems


def write_and_check(brain, root, commit, frozen, the_plan, helper_import, helper_rel, helper_src):
    edits = brain.write_patch(root, frozen, the_plan, helper_import)
    problems = check_patch(root, commit, the_plan, helper_rel, helper_src)
    missing = next((p for p in problems if p.startswith("not wrapped")), None)
    if missing and len(problems) == 1:
        todo = [e for e in the_plan["sites"] if e["slug"] in missing]
        edits += brain.write_patch(root, frozen, {"sites": todo}, helper_import,
                                   feedback=f"{missing}. Wrap them, then finish.")
        problems = check_patch(root, commit, the_plan, helper_rel, helper_src)
    if problems:
        raise PatchError("patch rejected: " + "; ".join(problems))
    freeze.verify(root, frozen)
    return edits


def routing_check(entry, stats, flags):
    """Problems with where the calls of a site's flip tests went in the confirm run (empty = as planned)."""
    seen = stats.get("by_test") or []
    if not seen:
        return ["no calls recorded for its flip tests"]
    problems = []
    rungs = {m for _, m in ladder.RUNGS}
    for bt in seen:
        modes, req, status = bt.get("modes", {}), bt.get("requested", {}), bt.get("status", {})
        if entry["kind"] == "keep":
            if bt.get("moved") or set(req) & rungs:
                problems.append(f"{bt['test']}: a guarded call was moved ({req})")
            continue
        allowed = {entry["model"], entry["fallback"]} - {None}
        if (flags or {}).get("isolated", True) and set(modes) - {"passthrough"}:
            problems.append(f"{bt['test']}: calls not moved by the helper ({modes})")
        if not bt.get("moved"):
            problems.append(f"{bt['test']}: no call went to the helper's endpoint")
        if set(req) - allowed and bt.get("moved"):
            problems.append(f"{bt['test']}: other models requested ({req})")
        if status.get("400"):
            problems.append(f"{bt['test']}: {status['400']} calls refused with 400 (a model Token Factory does not serve)")
    return problems[:5]


def confirm(run, the_plan, root, changed, embedding_model=None):
    """K runs of the flip tests of moved and guarded sites on the patched app; {site: confirm cell record}."""
    files = {rel: str(Path(root) / rel) for rel in changed}
    env = {"NEMOTRON_MOVE": "1", "NEMOTRON_MOVE_BASE_URL": f"{PROXY}/moved/v1", "NEMOTRON_MOVE_API_KEY": "mmd-proxy"}
    routes = {"default": {"mode": "old-model"}, "moved": {"mode": "passthrough"}, "spans": run.spans,
              **({"embedding_model": embedding_model} if embedding_model else {})}
    by_site = {e["site"]: e for e in the_plan["sites"] if e["site"] in run.flips}
    res = run.measure("confirm", None, list(by_site), {}, "confirm", routes=routes, env=env, files=files)
    out = {}
    for site, (outs, stats) in res.items():
        e = by_site[site]
        model = e["model"] if e["kind"] == "move" else ladder.SUPER  # a guard's cell is priced nowhere: any rung
        rec = run.record(site, "confirm", model, outs, stats, role="confirm" if e["kind"] == "move" else "confirm-guard")
        rec["routing_problems"] = routing_check(e, stats, run.state[site]["flags"])
        rec["requested"] = {k: v for bt in stats.get("by_test") or [] for k, v in bt.get("requested", {}).items()}
        if e["kind"] == "keep":
            rec.update(passes=not rec["routing_problems"], status="guard holds" if not rec["routing_problems"] else "guard leaked")
        elif rec["routing_problems"] and rec["passes"]:
            rec.update(passes=False, status="calls not routed as planned")
        out[site] = rec
    run.checkpoint()
    return out


def runs_compatible(doc, man, sha, name):
    """None if the ladder run's manifest version has the same rules and target entry as the current one."""
    if doc["manifest"].get("sha256") == sha:
        return None
    old = mf.version(doc["manifest"].get("sha256"))
    if old is None:
        return f"the ladder run used manifest {doc['manifest'].get('sha256')}, which no commit of census/MANIFEST.json has"
    if old.get("rules") != man.get("rules") or (old.get("targets") or {}).get(name) != man["targets"][name]:
        return f"the ladder run's manifest {doc['manifest'].get('sha256')[:12]} has other rules or another {name} entry"
    return None


def cmd_patch(name, ladder_run=None, clones=None, brain=None, run_job=None, box=None, ledger=None, check_pushed=True):
    from model_moving_day import agent, census
    clones = Path(clones or census.CLONES)
    man, sha = mf.load()
    problems = mf.verify(man, name, clones)
    if problems:
        raise mf.ManifestMismatch(problems)
    push = mf.pushed() if check_pushed else {}
    entry, targets = man["targets"][name], json.loads(mf.TARGETS.read_text())[name]
    runs = census.ladder_runs(name)
    if not runs:
        raise PatchError(f"no complete decision ladder run for {name}")
    if ladder_run:
        picked = [r for r in runs if r[0]["run_id"] == ladder_run]
        if not picked:
            raise PatchError(f"no complete decision run {ladder_run} for {name}")
        doc = picked[0][0]
    elif len(runs) > 1:
        raise PatchError(f"{len(runs)} complete decision runs for {name}: name one with --ladder-run "
                         f"({', '.join(r[0]['run_id'] for r in runs)}) and record why")
    else:
        doc = runs[0][0]
    why = runs_compatible(doc, man, sha, name)
    if why:
        raise PatchError(why)
    out = census.out_dir(name) / "patch"
    out.mkdir(parents=True, exist_ok=True)
    clone, commit = clones / name, entry["commit"]
    root = Path(tempfile.mkdtemp(prefix=f"mmd-patch-{name}-")) / "wt"
    _git(clone, "worktree", "add", "--detach", str(root), commit)
    try:
        return _patch_in(name, root, commit, doc, runs, man, sha, push, entry, targets, out, brain, run_job, box, ledger)
    finally:  # the clone's own working tree was never touched; the branch (if any) stays in its repository
        subprocess.run(["git", "worktree", "remove", "--force", str(root)], cwd=clone, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=clone, capture_output=True)


def _patch_in(name, root, commit, doc, runs, man, sha, push, entry, targets, out, brain, run_job, box, ledger):
    from model_moving_day import agent, census
    frozen = freeze.Frozen(entry["frozen"]["commit"], sorted({t for ts in entry["flip_tests"].values() for t in ts}),
                           entry["frozen"]["hashes"], entry["frozen"]["extra_paths"])
    freeze.verify(root, frozen)
    the_plan = plan(doc, root, find.scan(root).sites)
    (out / "plan.json").write_text(json.dumps({"ladder_run": doc["run_id"], **the_plan}, indent=1))
    pkg = entry["helper_package"]
    helper_rel, helper_import = f"{pkg}/nemotron_move.py", f"{pkg.replace('/', '.')}.nemotron_move"
    edits, conf, committed = [], {}, None
    if any(e["kind"] == "move" for e in the_plan["sites"]):
        helper_src = render_helper(the_plan)
        (root / helper_rel).write_text(helper_src)
        brain = brain or agent.Brain(tf.Client("c1"), agent.pick_brain(root), out / "agent-steps.jsonl")
        edits = write_and_check(brain, root, commit, frozen, the_plan, helper_import, helper_rel, helper_src)
        changed = changed_paths(root)
        (out / "patch.diff").write_text(_git(root, "diff") + "".join(
            f"\n--- new file {p}\n" + (root / p).read_text() for p in changed if p == helper_rel))
        spans = find.site_spans(find.scan(root).sites)
        run = census.Ladder(name, entry, man["rules"], man["rules"]["k"], [], "confirm", census.out_dir(name),
                            run_job or runner.run_job, box=box or sb.Sandbox(), ledger=ledger or tf.Ledger(),
                            manifest={"sha256": sha, **push}, cap=man["budgets"]["cap_usd_per_job"], spans=spans)
        run.event("start", kind="confirm", ladder_run=doc["run_id"])
        try:
            conf = confirm(run, the_plan, root, changed, entry.get("embedding_model"))
            run.doc["status"] = "complete"
        except BaseException as e:
            run.doc.update(status="aborted", error=f"{type(e).__name__}: {e}"[:800])
            raise
        finally:
            run.doc["ended_utc"] = census._utc()
            run.event("end", status=run.doc["status"])
            run.checkpoint()
        for e in the_plan["sites"]:  # a moved site that did not pass the confirm run stays on the old model
            c = conf.get(e["site"])
            if e["kind"] == "move" and not (c and c["passes"]):
                e.update(kind="keep", config={"keep": True}, not_moved_because=(c or {}).get("status", "not confirmed"))
        helper_src = render_helper(the_plan)
        (root / helper_rel).write_text(helper_src)
    moved = [e for e in the_plan["sites"] if e["kind"] == "move"]
    cov = json.loads((census.out_dir(name) / "coverage.json").read_text())
    base = man["baseline_before"]
    text = pr.render({"name": name, "github": entry["github"], "commit": entry["commit"], "license": entry["license"]},
                     doc, cov, {"model": base["model"], "price": tuple(base["usd_per_1m_tokens"]),
                                "source": base["source"], "retiring_model": targets.get("retiring_model", "-")},
                     edits=edits, confirm=conf, manifest=man, plan=the_plan,
                     runs=[{"run_id": d["run_id"], "started_utc": d.get("started_utc"), "used": d["run_id"] == doc["run_id"]}
                           for d, _ in runs])
    (out / "PR.md").write_text(text)
    (out / "plan.json").write_text(json.dumps({"ladder_run": doc["run_id"], **the_plan}, indent=1))
    if moved:
        (root / PR_FILE).write_text(text)
        committed = pr.commit_to_local_branch(root, BRANCH, changed_paths(root),
                                              f"Move {len(moved)} LLM call sites to NVIDIA Nemotron on Token Factory "
                                              f"(opt-in: NEMOTRON_MOVE=1)", PR_FILE)
    result = {"ladder_run": doc["run_id"], "moved": [e["site"] for e in moved],
              "kept": {e["site"]: e.get("not_moved_because", "guard") for e in the_plan["sites"] if e["kind"] == "keep"},
              "branch": BRANCH if committed else None, "commit": committed, "pr": str(out / "PR.md"),
              "confirm": {s: c["status"] for s, c in conf.items()}}
    (out / "result.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result))
    return result
