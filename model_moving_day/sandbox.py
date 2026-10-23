"""Run commands in Nebius Token Factory Sandboxes (contree-sdk) and keep model spend inside a ledger envelope.

A sandbox run cannot see the shared spend ledger on the host, so before a run that may call Token Factory we
reserve a fixed envelope in the shared ledger; inside the sandbox `tf_client` runs against a private ledger
capped at that envelope; afterwards the envelope is settled to the private ledger's total. If the total is
never reported back, the whole envelope stays reserved.
"""
import json, time
from dataclasses import dataclass

JSON_MARK = "<<<MMD-JSON>>>"


class SandboxCostShown(RuntimeError):
    """The sandbox reported a non-zero cost; Sandboxes are supposed to be free during the beta."""


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
    def __init__(self, contree=None):
        if contree is None:
            from contree_sdk import ContreeSync
            contree = ContreeSync()
        self.contree = contree

    def run(self, image, shell, env=None, files=None, timeout=900, tag=None, disposable=True, truncate=1 << 20):
        img = self.contree.images.use(image)
        t0 = time.time()
        done = img.run(shell=shell, env=env or {}, files=files or {}, timeout=timeout, tag=tag,
                       disposable=disposable, truncate_output_at=truncate).wait()
        res = done.result
        out = RunResult(res.exit_code, _text(res.stdout), _text(res.stderr), res.elapsed_time.total_seconds(),
                        round(time.time() - t0, 3), float(res.cost or 0.0), str(done.uuid) if done.uuid else "")
        if out.cost > 0:
            raise SandboxCostShown(f"sandbox run reported cost {out.cost} (image {image}, uuid {out.image_uuid})")
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
