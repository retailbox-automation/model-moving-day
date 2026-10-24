"""The judges' demo (REPLAY): its data equals CENSUS.md and the ladder files, the build is deterministic, the API answers
only exact keys, and 10 visitors at once get fast answers without errors."""
import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from demo import build, server

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def payload():
    return json.loads(build.OUT.read_text())


def _report():
    spec = importlib.util.spec_from_file_location("census_report_t", ROOT / "census" / "report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_is_deterministic_and_committed_file_is_current():
    first, second = build.dumps(build.build()), build.dumps(build.build())
    assert first == second
    assert build.OUT.read_text() == first, "demo/data/census.json is stale: run python -m demo.build"


def test_census_md_renders_from_the_demo_data(payload):
    # CENSUS.md and the demo share one source; rendering the demo's JSON must give CENSUS.md byte for byte
    assert _report().render(payload["census"]) == (ROOT / "CENSUS.md").read_text()


def test_every_cell_equals_its_ladder_file_and_its_census_row(payload):
    census_md = (ROOT / "CENSUS.md").read_text()
    n = 0
    for rid, run in payload["census"]["runs"].items():
        ladder = json.loads((ROOT / "evidence" / "census" / run["target"] / f"ladder-{rid}.json").read_text())
        assert ladder["kind"] == "decision" and run["k"] == ladder["k"]
        section = census_md.split(f"#### Run `{rid}`", 1)[1].split("#### Run", 1)[0]
        cells = [(s, c) for s, v in ladder["sites"].items() for c in v["cells"]]
        assert len(cells) == len(run["cells"])
        for (site, c), d in zip(cells, run["cells"]):
            r = c.get("runs_with_retried_infra") or {}
            assert (d["site"], d["rung"], d["params"], d["status"]) == (site, c["rung"], c["params"], c["status"])
            assert (d["passed_runs"], d["valid_runs"], d["infra_runs"]) == (c["passed_runs"], c["valid_runs"], c["infra_runs"])
            assert (d["set_aside_passed"], d["set_aside_failed"]) == (r.get("pass", 0), r.get("fail", 0))
            assert d["usd_per_1000_calls"] == c["usd_per_1000_calls"]
            row = (f"| `{site}` | {d['role']} | {d['rung']} | {d['params'] or '-'} | {d['status']} | "
                   f"{d['passed_runs']}/{d['valid_runs']} | {d['infra_runs']} ({d['set_aside_passed']}, {d['set_aside_failed']}) |")
            assert row in section
            n += 1
    on_disk = {f.stem.removeprefix("ladder-") for f in (ROOT / "evidence" / "census").glob("*/ladder-*.json")
               if json.loads(f.read_text())["kind"] == "decision"}
    assert on_disk == set(payload["census"]["runs"]) and n == 24  # every cell of every decision run (9 + 3 + 3 x 4)


def test_site_rows_equal_the_census_table_and_the_chosen_cells(payload):
    census_md = (ROOT / "CENSUS.md").read_text()
    for row in payload["census"]["sites"]:
        line = next(x for x in census_md.splitlines() if x.startswith(f"| {row['target']} | `{row['site']}` |"))
        assert f"| {row['flip_tests']} |" in line
        run = payload["census"]["runs"][row["headline_run"]]
        assert run["site_status"][row["site"]] == row["status"]
        if row["status"] == "chosen":
            assert f"**{row['chosen']}**" in line and f"${row['after_usd_per_1000']:.3f}" in line
            chosen = [c for c in run["cells"] if c["site"] == row["site"] and c["role"] == "ladder" and c["status"] == "pass"]
            assert chosen and chosen[0]["rung"] == row["chosen"]


@pytest.fixture(scope="module")
def live():
    httpd = server.serve(server.App(), host="127.0.0.1", port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_routes_answer_from_the_data(live, payload):
    c = payload["census"]
    st, about = _get(live + "/")
    assert st == 200 and about["endpoints"] and len(about["sites"]) == len(c["sites"])
    assert _get(live + "/api/census") == (200, c)
    for row in c["sites"]:
        st, site = _get(live + "/api/site/" + row["site"])
        assert st == 200 and site["row"] == row and site["cells"]
    for rid in c["runs"]:
        assert _get(live + "/api/run/" + rid) == (200, c["runs"][rid])
    for p in c["patches"]:
        st, body = _get(live + "/api/patch/" + p["target"])
        assert st == 200 and body["diff"] == payload["patch_files"][p["target"]]["diff"] and body["pr_md"]


@pytest.mark.parametrize("path", ["/api/site/nope.py:1", "/api/run/20000101T000000Z-000000", "/api/patch/paper-qa",
                                  "/api/patch/../../etc/passwd", "/api/patch/..%2f..%2fCENSUS.md",
                                  "/api/site/%2e%2e/%2e%2e/demo/data/census.json", "/../../etc/passwd", "/api/nothing",
                                  "/demo/data/census.json"])
def test_unknown_and_traversal_paths_are_404(live, path):
    st, body = _get(live + path)
    assert st == 404 and "error" in body


def test_only_get(live):
    assert _get(live + "/api/census", method="POST")[0] == 405


def test_ten_visitors_fifty_requests_each(live, payload):
    c = payload["census"]
    paths = (["/", "/api/census"] + ["/api/site/" + s["site"] for s in c["sites"]] + ["/api/run/" + r for r in c["runs"]]
             + ["/api/patch/" + p["target"] for p in c["patches"]])
    times, errors, lock = [], [], threading.Lock()

    def visitor(i):
        for j in range(50):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(live + paths[(i + j) % len(paths)], timeout=10) as r:
                    r.read()
                    ok = r.status == 200
            except Exception as e:  # counted, not swallowed
                ok = False
                with lock:
                    errors.append(repr(e))
            with lock:
                times.append(time.perf_counter() - t0)
                if not ok and not errors:
                    errors.append("non-200")
    threads = [threading.Thread(target=visitor, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    times.sort()
    assert len(times) == 500 and errors == []
    assert times[int(0.95 * len(times)) - 1] < 0.3
