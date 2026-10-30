"""The static REPLAY page (demo/static): its data is census.json plus what the manifest and the ladder files say, the
generated files are current, and the rendered page shows only numbers that CENSUS.md or README.md already state, with no
page errors, no requests off the page except the visit counter, and no sideways scroll from 390 to 1920 px. The first
screen (three receipts, funnel, date strip, re-run tile) is checked against the census, SCAN.md and the re-run's RESULT.md."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from demo import build, static_build

ROOT = Path(__file__).resolve().parents[1]
PW = os.environ.get("PLAYWRIGHT_MODULE", "")  # path to node_modules/playwright; the page checks are skipped without it
TRACKER = "https://t.retailbox-automation.com/t.js"


def _data_js():
    s = (static_build.STATIC / "data.js").read_text()
    assert s.startswith("window.MMD = ") and s.endswith(";\n")
    return json.loads(s[len("window.MMD = "):-2])


def test_generated_files_are_current():
    assert static_build.main(["check"]) == 0, "run: python -m demo.static_build"


def test_data_js_is_census_json_plus_details():
    d = _data_js()
    details = d.pop("details")
    cast = d.pop("rerun_cast")
    assert d == json.loads(build.OUT.read_text())
    # the re-run recording rides along as text (its player works from file://) and as a file next to the page
    assert cast == (ROOT / d["rerun"]["cast"]).read_text() == (static_build.STATIC / "rerun.cast").read_text()
    manifest = json.loads((ROOT / "census" / "MANIFEST.json").read_text())
    for site, x in details.items():
        target = site.split("/")[0] if not site.startswith("src/") else "paper-qa"
        target = {"llm": "llm", "instructor": "instructor"}.get(target, target)
        assert x["flip_tests"] == manifest["targets"][target]["flip_tests"][site]
        ladder = json.loads((ROOT / "evidence" / "census" / target / f"ladder-{x['run_id']}.json").read_text())
        assert [c["per_test_passed"] for c in x["cells"]] == [c.get("per_test_passed") or {} for c in ladder["sites"][site]["cells"]]
    decided = [s["site"] for s in d["census"]["sites"]]
    assert sorted(details) == sorted(decided)


def test_noindex_and_no_keys():
    html = (static_build.STATIC / "index.html").read_text()
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert (static_build.STATIC / "_headers").read_text() == "/*\n  X-Robots-Tag: noindex, nofollow\n"
    for f in static_build.STATIC.iterdir():
        t = f.read_text()
        assert not re.search(r"(sk-|nebius_|NEBIUS_API_KEY=|v1\.CmQ)", t), f.name


@pytest.fixture(scope="module")
def page():
    if not shutil.which("node") or not PW or not Path(PW).exists():
        pytest.skip("node or Playwright not available")
    r = subprocess.run(["node", str(ROOT / "demo" / "static_page_check.js")], capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PLAYWRIGHT_MODULE": PW})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("width", ["1920", "390"])
def test_page_renders_clean(page, width):
    p = page[width]
    assert p["errors"] == []
    assert set(p["external"]) <= {TRACKER}
    assert p["scrollWidth"] == int(width)


@pytest.mark.parametrize("width", ["1920", "390"])
def test_every_dollar_amount_on_the_page_is_stated_in_census_or_readme(page, width):
    stated = (ROOT / "CENSUS.md").read_text() + (ROOT / "README.md").read_text()
    shown = set(re.findall(r"\$\d+\.\d+", page[width]["text"] + "".join(d["text"] for d in page[width]["details"].values())))
    assert shown, "no dollar amounts found: the check is blind"
    assert sorted(x for x in shown if x not in stated) == []


def test_board_rows_and_chips_match_the_census(page):
    c = json.loads(build.OUT.read_text())["census"]
    chosen = [s for s in c["sites"] if s["status"] == "chosen"]
    p = page["1920"]
    assert p["rows"] == [s["site"] for s in chosen] + ["target:paper-qa"]
    assert p["chips"] == [s["chosen"] + (" + reasoning off" if s["fix"] == "reasoning_off" else "") for s in chosen]
    for s in chosen:
        assert s["site"].split("/")[-1] in p["details"][s["site"]]["h2"]
    assert "infrastructure" in p["details"]["target:paper-qa"]["text"]


@pytest.mark.parametrize("width", ["1280", "1366", "1440", "1536", "1680"])
def test_board_fits_laptop_widths(page, width):
    f = page["fit"][width]
    assert f["page"] == int(width)
    assert f["table"] <= f["board"] + 1, f"board clipped at {width}px: table {f['table']} > card {f['board']}"


# ---- first screen ----

def _census():
    return json.loads(build.OUT.read_text())


def _routable(c):
    """Call sites left after the Responses API and embedding sites were set aside before the ladders."""
    nm = c["census"]["not_measured"]
    resp = sum(1 for x in nm if "Responses API" in x["why"])
    emb = sum(1 for x in nm if "embedding call site" in x["why"])
    return c["census"]["headline"]["sites_found"] - resp - emb, resp, emb


def test_routable_sites_are_derived_from_the_not_measured_reasons():
    n, resp, emb = _routable(_census())
    assert (n, resp, emb) == (17, 6, 3)
    readme = (ROOT / "README.md").read_text()
    assert "At 8 of the other 17 the repositories' own tests can decide" in readme


def test_first_screen_funnel_and_note(page):
    c = _census()
    h = c["census"]["headline"]
    f = page["first"]
    assert f["funnel"] == [[h["sites_found"], str(h["sites_found"]), "LLM call sites found"],
                           [_routable(c)[0], str(_routable(c)[0]), "left after 9 set aside"],
                           [h["sites_in_ladder"], str(h["sites_in_ladder"]), "where tests notice a wrong answer, a live model can pass"],
                           [h["decided"], str(h["decided"]), f"decided ({', '.join(h['repositories_decided'])})"]]
    assert [x[0] for x in f["funnel"]] == [26, 17, 8, 4]
    assert f["fnote"] == ("set aside: 6 Responses API (our proxy does not route them), 3 embedding (fixed model) · "
                          "paper-qa: no decision (503/500, lost jobs) · our coverage gate not met")


def test_first_screen_dollars_are_in_census_or_rerun_result(page):
    stated = (ROOT / "CENSUS.md").read_text() + (ROOT / "evidence" / "rerun-2026-10-29" / "RESULT.md").read_text()
    f = page["first"]
    shown = re.findall(r"\$\d+\.\d+", f["text"] + f["panel"]["side"])
    assert len(set(shown)) >= 9, shown  # 6 on the receipts, 4 in the re-run panel
    assert sorted(x for x in set(shown) if x not in stated) == []


def test_first_screen_receipts_follow_the_census(page):
    c = _census()
    d = _data_js()
    k = next(iter(c["census"]["runs"].values()))["k"]
    sites = [s for s in c["census"]["sites"] if s["status"] == "chosen" and not s["note"]]
    cards = page["first"]["cards"]
    assert [x["site"] for x in cards] == [s["site"] for s in sites] == [
        "instructor/v2/core/retry.py:224", "instructor/v2/core/retry.py:523", "llm/default_plugins/openai_models.py:1494"]
    for card, s in zip(cards, sites):
        run = c["census"]["runs"][d["details"][s["site"]]["run_id"]]
        cells = [x for x in run["cells"] if x["site"] == s["site"]]
        off = lambda x: (x["params"] or {}).get("reasoning") == "off"
        ch = next(x for x in cells if x["role"] == "ladder" and x["rung"] == s["chosen"] and off(x) == bool(s["fix"]))
        fb = next(x for x in cells if x["role"] == "fallback")
        assert card["chip"] == s["chosen"]
        assert card["money"] == f"${ch['usd_per_1000_calls']:.3f} per 1,000 calls · next bigger, {fb['rung']}: ${fb['usd_per_1000_calls']:.3f}"
        # the fallback cell is drawn from the data: green dots for its passed runs and a "fallback" label
        fbrow = [r for r in card["rows"] if r["fallback"]]
        assert [r["name"] for r in fbrow] == [fb["rung"]]
        assert fbrow[0]["dots"] == "p" * fb["passed_runs"] + "f" * (fb["valid_runs"] - fb["passed_runs"])
        for r in card["rows"]:
            assert len(r["dots"]) == k
    down, stayed, remedy = cards
    assert down["tag"] == "MOVED DOWN" and down["beside"] == "8 repo tests × 5 runs"
    assert [r["dots"] for r in down["rows"]] == ["ppfff", "ppppp", "ppppp", "nnnnn"]  # Nano 2 of 5 at :224
    lt = next(x for x in d["details"][stayed["site"]]["cells"] if x["rung"] == "Lightning")
    bad = {t: v for t, v in lt["per_test_passed"].items() if v[0] < v[1]}
    assert len(lt["per_test_passed"]) - len(bad) == 22 and len(lt["per_test_passed"]) == 23
    assert [(t.split("::")[-1].split("[")[0], v[0]) for t, v in bad.items()] == [("test_nested_model_extraction", 0)]
    assert stayed["tag"] == "STAYED ON SUPER: one test said no"
    assert " ".join(stayed["beside"].split()) == "Lightning passed 22 of 23 tests; test_nested_model_extraction failed in every run"
    assert [r["dots"] for r in stayed["rows"]] == ["fffff", "fffff", "ppppp", "ppppp"]  # Lightning 0 of 5 at :523
    ratio = next(s for s in sites if s["site"] == stayed["site"])["after_over_before"]
    assert stayed["more"] == f"{ratio:.1f}× OpenAI's priced replacement, shown as is" == "2.9× OpenAI's priced replacement, shown as is"
    assert remedy["tag"] == "MOVED WITH A REMEDY LISTED IN ADVANCE" and remedy["chip"] == "Nano"
    assert [(r["name"], r["dots"]) for r in remedy["rows"]] == [("Nano", "fffff"), ("Nano", "ppppp"), ("Lightning", "ppppp")]
    assert remedy["note"] == "'\\nYES' ≠ 'YES'"
    assert " ".join(remedy["fix"].split()) == "↓ reasoning off"
    assert remedy["rule"] == "rule-backed, not a discovery" and remedy["beside"] == "1 repo test × 5 runs"


def test_first_screen_roles_come_from_the_agent_step_logs(page):
    b = _census()["brains"]
    assert b["llm"]["diagnose_models"] == b["llm"]["patch_models"] == ["nvidia/nemotron-3-super-120b-a12b"]
    assert b["instructor"]["patch_models"] == ["nvidia/Nemotron-3-Ultra-550b-a55b"] and b["instructor"]["diagnose_models"] == []
    assert page["first"]["roles"] == ["Nano · Lightning · Super · Ultra answer the calls of the repos' tests",
                                      "Nemotron Super diagnoses and writes the branch (llm)",
                                      "Nemotron Ultra writes the branch (instructor)"]


def test_first_screen_date_strip_and_scan_line(page):
    c = _census()
    f = page["first"]
    y, m, d = re.search(r"retiring (\d{4})-(\d{2})-(\d{2})", c["census"]["manifest"]["baseline"]["why"]).groups()
    assert (y, m, d) == ("2027", "01", "14")
    assert f["date"] == "January 14, 2027:" and f["calendar"] == ["January", "14", "2027"]
    assert f["what"] == "OpenAI shuts down GPT-4o, GPT-4o mini, GPT-4.1 and GPT-4.1 mini"
    assert f["source"] == "https://platform.openai.com/docs/deprecations"
    scan_md = (ROOT / c["scan"]["dir"] / "SCAN.md").read_text()
    t, k = map(int, re.search(r"\*\*T\*\* \(retiring ID in a test `\.py` file\): (\d+) of (\d+)", scan_md).groups())
    assert (t, k) == (c["scan"]["T"], c["scan"]["K"]) == (37, 50)
    assert f["scan"]["text"] == (f"{t} of {k} popular Python LLM repositories still name them in their tests "
                                 "(static scan, rule pushed before the scan).")
    assert f["scan"]["href"] == f"https://github.com/retailbox-automation/model-moving-day/blob/main/{c['scan']['dir']}/SCAN.md"
    assert page["noScan"]["hidden"] and "popular Python" not in page["noScan"]["text"]


def test_first_screen_shows_the_tavily_notice_check(page):
    n = _census()["notices"]
    x = page["first"]["notice"]
    assert n["live"] and n["confirmed"] == n["families"] == 4
    assert x["text"] == "✓ 4 of 4 confirmed with Tavily, live Oct 30, 2026"
    assert x["href"] == f"https://github.com/retailbox-automation/model-moving-day/blob/main/{n['dir']}/NOTICES.md"
    assert "2026-10-14: GPT-4 series model deprecations" in x["title"] and "developers.openai.com" in x["title"]


def test_first_screen_fits_1920x1200_with_the_board_peeking(page):
    f = page["first"]
    assert f["errors"] == []
    assert f["tileBottom"] <= 1200 and f["boardTop"] < 1200 - 20
    assert f["pills"][0] == "Replay of a recorded census · no model calls" and f["tracker"]


@pytest.mark.parametrize("width", ["390", "1280", "1440", "1536", "1680", "1920"])
def test_first_screen_has_no_sideways_overflow(page, width):
    x = page["firstFit"][width]
    assert x == {"page": int(width), "clipped": [], "outside": []}


def test_branch_diff_links_open_the_patch_view(page):
    for site, x in page["first"]["diffs"].items():
        assert x["h2"].endswith(site.split("/")[-1]) and x["diff"], site
        assert x["hash"] == "#site=" + site.replace("/", "%2F").replace(":", "%3A")


def test_rerun_tile_and_panel(page):
    r = _census()["rerun"]
    f = page["first"]
    assert f["tile"] == "Watch the agent re-run llm :1494 (terminal recording, not part of the census)"
    p = f["panel"]
    assert p["open"]
    assert f"{r['calls']} calls, {r['calls_with_id']} with a Token Factory response id; model cost ${r['model_cost_usd']}" in p["side"]
    assert (r["calls"], r["calls_with_id"]) == (68, 68)
    for i in r["first_ids"]:
        assert i in p["side"]
    assert p["links"][0] == "https://github.com/retailbox-automation/model-moving-day/blob/main/" + r["result"]
    assert p["links"][1] == "rerun.cast"
    assert "not part of the census" in p["head"]
    # the player prints the whole recording, last line included
    assert "re-run of one call site" in p["term"] and p["term"].rstrip().endswith("UTC")
