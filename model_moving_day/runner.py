"""Host side of a sandbox run: prepare a target image once, then run jobs (proxy + the target's own tests) in it.

Model spend inside a job is capped by an envelope reserved in the shared ledger before the run and settled to the
in-sandbox ledger total afterwards (sandbox.Envelope). The sandbox's own reported cost is accounted separately by
sandbox.Sandbox.run.
"""
import json, os
from dataclasses import dataclass, field
from pathlib import Path

from model_moving_day import sandbox as sb

PKG = Path(__file__).resolve().parent
BASE_IMAGE = "python:3.12-slim"
SANDBOX_MAX_S, JOB_OVERHEAD_S = 3600, 300  # Sandboxes stop a run at 3600 s; upload, proxy start and summary margin
TEST_UID = 10001  # the target's tests run under this uid inside the sandbox, apart from the proxy that holds the key
KEY_FILE = "/mmd/secret/tf.key"


@dataclass
class Target:
    name: str
    github: str                      # owner/repo
    commit: str
    install: list                    # pip requirement strings installed next to the editable repo
    pytest_args: list
    env: dict = field(default_factory=dict)
    exclusions: dict = field(default_factory=dict)  # path or node id -> reason


def package_files():
    """Our package as sandbox upload specs: /mmd/model_moving_day/... -> local path."""
    files = {}
    for p in sorted(PKG.rglob("*.py")):
        files[f"/mmd/model_moving_day/{p.relative_to(PKG).as_posix()}"] = str(p)
    return files


def prepare_script(t):
    reqs = " ".join(f"'{r}'" for r in t.install)
    return f"""set -e
mkdir -p /work
python - <<'PY'
import io, tarfile, urllib.request
data = urllib.request.urlopen("https://codeload.github.com/{t.github}/tar.gz/{t.commit}", timeout=300).read()
tarfile.open(fileobj=io.BytesIO(data)).extractall("/work/src", filter="data")
PY
mv /work/src/* /work/repo
pip install -q --disable-pip-version-check uv
uv pip install --system -q -e /work/repo {reqs}
pip freeze --disable-pip-version-check > /work/pip-freeze.txt
echo "<<<MMD-JSON>>>{{\\"prepared\\": \\"{t.name}\\", \\"packages\\": $(wc -l < /work/pip-freeze.txt)}}"
"""


def prepare(box, t):
    r = box.run(BASE_IMAGE, prepare_script(t), timeout=1800, disposable=False, tag=f"mmd-{t.name}:{t.commit[:12]}")
    return r, sb.trailing_json(r.stdout)


def timeouts(k, timeout_s, parallel):
    """(timeout per pytest run, sandbox timeout). In parallel the K runs share one window; in sequence each run gets
    an equal share of what the sandbox allows."""
    if parallel:
        run_s = min(timeout_s, SANDBOX_MAX_S - JOB_OVERHEAD_S)
        return run_s, min(SANDBOX_MAX_S, run_s + JOB_OVERHEAD_S)
    run_s = min(timeout_s, (SANDBOX_MAX_S - JOB_OVERHEAD_S) // k)
    return run_s, min(SANDBOX_MAX_S, k * run_s + JOB_OVERHEAD_S)


def run_job(box, image, t, routes, k, cap_usd, ledger, extra_files=None, frozen_hashes=None, frozen_extra=(),
            require_frozen=False, timeout_s=3000, parallel=True, note="", extra_args=(), proxy_env=True, env=None):
    run_s, box_s = timeouts(k, timeout_s, parallel)
    spec = {"repo_dir": "/work/repo", "routes": routes, "k": k, "pytest_args": [*t.pytest_args, *extra_args],
            "env": dict(t.env, **(env or {})), "cap_usd": cap_usd, "timeout_s": run_s, "port": 8765,
            "frozen_hashes": frozen_hashes or {}, "frozen_extra": list(frozen_extra), "require_frozen": require_frozen,
            "proxy_env": proxy_env, "parallel": parallel, "test_uid": TEST_UID, "key_file": KEY_FILE}
    files = dict(package_files(), **{"/mmd/spec.json": json.dumps(spec).encode(),
                                     KEY_FILE: os.environ["NEBIUS_API_KEY"].encode()})  # a file, not the run's env
    files.update({f"/work/repo/{rel}": src for rel, src in (extra_files or {}).items()})
    env_box = sb.Envelope(ledger, box.tag, cap_usd, f"{t.name} job {note}")
    r = box.run(image, "cd /mmd && python -m model_moving_day.job --spec /mmd/spec.json", timeout=box_s,
                env={"MMD_OUT": "/mmd/out"}, files=files)
    summary = sb.trailing_json(r.stdout)
    if summary is not None:
        try:
            env_box.settle(summary.get("envelope_spent", cap_usd))
        except ValueError as e:  # the in-sandbox cap broke: keep the whole envelope reserved and say so
            summary["envelope_error"] = str(e)[:300]
    return r, summary
