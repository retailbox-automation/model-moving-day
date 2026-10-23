"""Run commands in Nebius Token Factory Sandboxes (contree-sdk) and keep model spend inside a ledger envelope.

Sandbox runs report a `cost` field (the docs call it "for diagnostics only"; whether beta runs bill the balance is
not confirmed). We treat it as real spend: each run reserves RUN_RESERVE in the shared ledger first and settles
to the reported cost afterwards; a run that never reports keeps the whole reservation. Every run is logged
(operation id, image, command, env keys without values, times, cost) and the number of runs is capped.

A sandbox run cannot see the shared spend ledger on the host, so before a run that may call Token Factory we
reserve a fixed envelope in the shared ledger; inside the sandbox `tf_client` runs against a private ledger
capped at that envelope; afterwards the envelope is settled to the private ledger's total. If the total is
never reported back, the whole envelope stays reserved.
"""
import json, os, threading, time, uuid
from dataclasses import dataclass
from pathlib import Path

JSON_MARK = "<<<MMD-JSON>>>"
RUN_RESERVE = 0.01  # USD reserved per sandbox run until its reported cost is known
DEFAULT_LOG = Path(__file__).resolve().parents[1] / "evidence" / "sandbox" / "runs.jsonl"


class SandboxRunLimit(RuntimeError):
    """The agreed number of sandbox runs has been used up."""


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float   # server-side elapsed time of the operation
    wall_s: float      # client-side time including upload, queueing and polling
    cost: float
    image_uuid: str


def trailing_json(stdout):
    """The JSON object printed after JSON_MARK on the last marked line, or None."""
    idx = stdout.rfind(JSON_MARK)
    if idx < 0:
        return None
    return json.loads(stdout[idx + len(JSON_MARK):].splitlines()[0])


class Sandbox:
    def __init__(self, contree=None, ledger=None, tag="c1", log_path=None, max_runs=None, concurrency=4):
        if contree is None:
            from contree_sdk import ContreeSync
            contree = ContreeSync()
        if ledger is None:
            from model_moving_day.tf_client import Ledger
            ledger = Ledger()
        self.contree, self.ledger, self.tag = contree, ledger, tag
        self.log_path = Path(log_path or os.environ.get("MMD_SANDBOX_LOG", DEFAULT_LOG))
        self.max_runs = int(max_runs if max_runs is not None else os.environ.get("MMD_SANDBOX_MAX_RUNS", 40))
        self._slots, self._lock, self._ops = threading.BoundedSemaphore(concurrency), threading.Lock(), {}
        start = getattr(contree, "_start_operation", None)
        if start is not None:  # remember the operation id of each run, keyed by the run's unique hostname
            async def _start(request):
                op = await start(request)
                self._ops[getattr(request, "hostname", "")] = str(op)
                return op
            contree._start_operation = _start

    def _count_and_claim(self):
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            n = sum(1 for line in open(self.log_path) if line.strip()) if self.log_path.exists() else 0
            if n >= self.max_runs:
                raise SandboxRunLimit(f"{n} sandbox runs logged in {self.log_path}, limit {self.max_runs}")

    def _log(self, rec):
        with self._lock, open(self.log_path, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def run(self, image, shell, env=None, files=None, timeout=900, tag=None, disposable=True, truncate=1 << 20):
        self._count_and_claim()
        host = f"mmd-{uuid.uuid4().hex[:12]}"
        rec = {"ts": round(time.time(), 3), "image": image, "command": shell, "env_keys": sorted(env or {}),
               "files": sorted(files or {}), "hostname": host}
        with self._slots:
            self.ledger.reserve(self.tag, RUN_RESERVE, f"sandbox run {host}")
            t0 = time.time()
            try:
                img = self.contree.images.use(image)
                done = img.run(shell=shell, env=env or {}, files=files or {}, timeout=timeout, tag=tag, hostname=host,
                               disposable=disposable, truncate_output_at=truncate).wait()
                res = done.result
            except Exception as e:
                rec.update(error=f"{type(e).__name__}: {e}"[:500], wall_s=round(time.time() - t0, 3),
                           operation=self._ops.get(host), cost=None)
                self._log(rec)  # the reservation is kept: we do not know what the run cost
                raise
        cost = float(res.cost or 0.0)
        self.ledger.settle(self.tag, cost - RUN_RESERVE, f"sandbox run {host} reported cost")
        out = RunResult(res.exit_code, _text(res.stdout), _text(res.stderr), res.elapsed_time.total_seconds(),
                        round(time.time() - t0, 3), cost, str(done.uuid) if done.uuid else "")
        rec.update(operation=self._ops.get(host), exit_code=out.exit_code, elapsed_s=out.elapsed_s, wall_s=out.wall_s,
                   cost=cost, result_image=out.image_uuid)
        self._log(rec)
        return out


def _text(v):
    if v is None:
        return ""
    return v.decode(errors="replace") if isinstance(v, bytes) else str(v)


class Envelope:
    """A spend envelope reserved in the shared ledger before a sandbox run and settled once afterwards."""

    def __init__(self, ledger, tag, usd, note):
        self.ledger, self.tag, self.usd, self.note, self.closed = ledger, tag, usd, note, False
        ledger.reserve(tag, usd, f"envelope {note}")

    def settle(self, actual):
        if self.closed:
            raise RuntimeError(f"envelope {self.note} already settled")
        if actual > self.usd + 1e-9:
            raise ValueError(f"envelope {self.note}: reported ${actual:.6f} exceeds the envelope ${self.usd:.6f}")
        self.ledger.settle(self.tag, actual - self.usd, f"settle envelope {self.note}")
        self.closed = True
