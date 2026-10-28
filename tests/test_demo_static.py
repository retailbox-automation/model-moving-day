"""The static REPLAY page (demo/static): its data is census.json plus what the manifest and the ladder files say, the
generated files are current, and the rendered page shows only numbers that CENSUS.md or README.md already state, with no
page errors, no requests off the page except the visit counter, and no sideways scroll at 1920 or 390 px."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from demo import build, static_build

ROOT = Path(__file__).resolve().parents[1]
PW = os.environ.get("PLAYWRIGHT_MODULE", "/Users/oskolamicheal/Projects/amazon-alexa-plus-build/spike/node_modules/playwright")
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
    assert d == json.loads(build.OUT.read_text())
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
    if not shutil.which("node") or not Path(PW).exists():
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
