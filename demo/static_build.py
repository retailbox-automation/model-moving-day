"""Static REPLAY site in demo/static/: hand-written index.html, app.css, app.js plus four generated files.

    python -m demo.static_build           write demo/static/data.js, robots.txt, _headers, rerun.cast
    python -m demo.static_build check     exit 1 if any generated file differs from a fresh build

No server, no network, no keys: the page reads `window.MMD` from data.js, so it also works from file://. data.js is
demo/data/census.json (built by `python -m demo.build` from the same census/report.py data() that writes CENSUS.md)
unchanged, so every number on the page is a number in CENSUS.md. The recorded re-run's terminal recording (asciicast)
is copied next to the page and also carried in data.js as text, so its player works from file:// too. Output is
deterministic.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import build as B  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
GENERATED = ("data.js", "robots.txt", "_headers", "rerun.cast")


def details(census):
    """Per decided site, from the pushed manifest and the headline ladder file: the flip-test names and, per cell, how
    many of the K runs each flip test passed. Read as is; nothing is recomputed here."""
    manifest = json.loads((ROOT / "census" / "MANIFEST.json").read_text())
    out = {}
    for t in census["targets"]:
        flips = manifest["targets"][t["target"]]["flip_tests"]
        for run_id in t["runs"]:
            run = census["runs"][run_id]
            ladder = json.loads((ROOT / "evidence" / "census" / t["target"] / f"ladder-{run_id}.json").read_text())
            for site, s in ladder["sites"].items():
                if site in out and not run["headline"]:
                    continue
                out[site] = {
                    "run_id": run_id,
                    "flip_tests": flips.get(site, []),
                    "cells": [{"rung": c["rung"], "role": c.get("role"), "params": c.get("params") or {},
                               "per_test_passed": c.get("per_test_passed") or {}, "calls": c.get("calls"),
                               "tokens_per_call": c.get("tokens_per_call")} for c in s["cells"]],
                }
    return out


def files():
    data = B.build()
    data["details"] = details(data["census"])
    cast = (ROOT / data["rerun"]["cast"]).read_text() if data.get("rerun") else ""
    data["rerun_cast"] = cast or None
    js = "window.MMD = " + json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + ";\n"
    return {
        "data.js": js,
        "robots.txt": "User-agent: *\nDisallow: /\n",
        "_headers": "/*\n  X-Robots-Tag: noindex, nofollow\n",
        "rerun.cast": cast,
    }


def main(argv):
    out = files()
    if argv[:1] == ["check"]:
        bad = [n for n, t in out.items() if not (STATIC / n).exists() or (STATIC / n).read_text() != t]
        print("up to date" if not bad else "stale: " + ", ".join(bad))
        return 1 if bad else 0
    STATIC.mkdir(parents=True, exist_ok=True)
    for n, t in out.items():
        (STATIC / n).write_text(t)
        print(f"wrote demo/static/{n} ({len(t.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
