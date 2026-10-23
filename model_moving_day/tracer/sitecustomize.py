"""Call-site tracer, loaded by Python at start-up when this directory is on PYTHONPATH.

Patches httpx and httpx2 (openai 3.x, litellm and most LLM clients send through one of them) so every request
carries three headers:
x-mmd-site = "<path relative to MMD_REPO_ROOT>:<line>" of the innermost stack frame that belongs to the target
repo's own source (not its tests, not site-packages), x-mmd-chain = all such frames (innermost first, up to 8),
and x-mmd-test = $PYTEST_CURRENT_TEST. The recording proxy
logs both, which links each of the repo's tests to the call sites it really reaches.
"""
import os, sys

_ROOT = os.path.realpath(os.environ.get("MMD_REPO_ROOT", "")) + os.sep if os.environ.get("MMD_REPO_ROOT") else None
_SKIP = set((os.environ.get("MMD_SKIP_DIRS") or "tests,test,.venv,venv").split(","))


def _chain(limit=8):
    """Repo source frames (not tests, not site-packages), innermost first, as "path:line"."""
    out, f = [], sys._getframe(2)
    while f is not None and len(out) < limit:
        fn = os.path.realpath(f.f_code.co_filename)
        if _ROOT and fn.startswith(_ROOT) and "site-packages" not in fn:
            rel = fn[len(_ROOT):]
            base = os.path.basename(rel)
            if rel.split(os.sep)[0] not in _SKIP and not base.startswith("test_") and base != "conftest.py":
                out.append(f"{rel.replace(os.sep, '/')}:{f.f_lineno}")
        f = f.f_back
    return out


def _tag(request):
    try:
        chain = _chain()
        request.headers["x-mmd-site"] = chain[0] if chain else "unknown"
        request.headers["x-mmd-chain"] = "|".join(chain)
        request.headers["x-mmd-test"] = os.environ.get("PYTEST_CURRENT_TEST", "")
    except Exception as e:  # never break the app under test; say why on stderr
        print(f"[mmd-tracer] could not tag request: {type(e).__name__}: {e}", file=sys.stderr)


def _install():
    """Patch Client.send / AsyncClient.send of httpx and of httpx2 (the transport of openai 3.x)."""
    import importlib
    for name in ("httpx", "httpx2"):
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if getattr(mod.Client.send, "_mmd", False):
            continue
        sync_send, async_send = mod.Client.send, mod.AsyncClient.send

        def send(self, request, *a, _orig=sync_send, **kw):
            _tag(request)
            return _orig(self, request, *a, **kw)

        async def asend(self, request, *a, _orig=async_send, **kw):
            _tag(request)
            return await _orig(self, request, *a, **kw)

        send._mmd = asend._mmd = True
        mod.Client.send, mod.AsyncClient.send = send, asend


if _ROOT:
    _install()
