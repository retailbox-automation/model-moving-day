"""Build demo/data/census.json from the evidence: the same dict that renders CENSUS.md (census/report.py data()), plus
the patch texts (PR.md, patch.diff) of each migration branch. Deterministic: building twice gives the same bytes.

    .venv/bin/python -m demo.build          # writes demo/data/census.json
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "data" / "census.json"
ABOUT = {
    "what": "Model Moving Day: which NVIDIA Nemotron model on Nebius Token Factory each LLM call site needs, decided by "
            "the repository's own tests. This demo replays the recorded census; it makes no model calls.",
    "source": "Every number comes from the evidence files in this repository, through census/report.py (the same "
              "function writes CENSUS.md). To run the census yourself with your own Token Factory key, see README.md.",
    "honesty": "Test traffic, not production traffic. The coverage gate written in our internal notes before the runs "
               "(3 repositories, 5 independent decisions; not in the pushed manifest) was not met. Limits and what is not claimed: README.md and CENSUS.md.",
}


def _report():
    spec = importlib.util.spec_from_file_location("census_report", ROOT / "census" / "report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build():
    census = _report().data()
    files = {}
    for p in census["patches"]:
        d = ROOT / "evidence" / "census" / p["target"] / "patch"
        files[p["target"]] = {"pr_md": (d / "PR.md").read_text(), "diff": (d / "patch.diff").read_text()}
    return {"schema": 1, "about": ABOUT, "census": census, "patch_files": files}


def dumps(obj):
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(dumps(build()))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
