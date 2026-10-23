"""Nemotron move helper (added by Model Moving Day, MIT). Off unless NEMOTRON_MOVE=1.

With NEMOTRON_MOVE=1 and OPENAI_BASE_URL pointing at Nebius Token Factory, each moved call site sends the
Nemotron model the census chose for it, with the parameter fixes the census measured, instead of the model the
caller named. NEMOTRON_MODEL_<SITE> overrides one site. On HTTP 404 (model retired or renamed: Token Factory does
not reroute retired models) the call is retried once on the site's fallback model, with a log line. On the first
moved call the helper lists Token Factory's /models and logs a warning for any configured model that is missing.
"""
import json, logging, os, threading, urllib.request

log = logging.getLogger("nemotron_move")

# site -> {"model": chosen rung, "fallback": next rung up, "params": request fixes merged into the call}
SITES = {}  # filled in by the migration patch

_checked, _lock = False, threading.Lock()


def enabled():
    return os.environ.get("NEMOTRON_MOVE", "").lower() in ("1", "true", "yes")


def _site_env(site):
    return "NEMOTRON_MODEL_" + "".join(c if c.isalnum() else "_" for c in site).upper()


def model_for(site, requested):
    """The model this call site should send: the caller's choice unless the move is enabled."""
    if not enabled() or site not in SITES:
        return requested
    return os.environ.get(_site_env(site)) or SITES[site]["model"]


def _prepare(site, kwargs):
    if not enabled() or site not in SITES:
        return kwargs, None
    check_models()
    kw = dict(kwargs)
    kw["model"] = model_for(site, kw.get("model"))
    for k, v in SITES[site].get("params", {}).items():
        if isinstance(v, dict) and isinstance(kw.get(k), dict):
            kw[k] = {**kw[k], **v}
        else:
            kw[k] = v
    for k in SITES[site].get("drop", []):
        kw.pop(k, None)
    return kw, SITES[site].get("fallback")


def _is_404(exc):
    return getattr(exc, "status_code", None) == 404 or getattr(getattr(exc, "response", None), "status_code", None) == 404


def call(site, create, *args, **kwargs):
    kw, fallback = _prepare(site, kwargs)
    try:
        return create(*args, **kw)
    except Exception as e:
        if not (fallback and _is_404(e)):
            raise
        log.warning("nemotron_move: %s returned 404 at %s, retrying on fallback %s", kw.get("model"), site, fallback)
        return create(*args, **dict(kw, model=fallback))


async def acall(site, create, *args, **kwargs):
    kw, fallback = _prepare(site, kwargs)
    try:
        return await create(*args, **kw)
    except Exception as e:
        if not (fallback and _is_404(e)):
            raise
        log.warning("nemotron_move: %s returned 404 at %s, retrying on fallback %s", kw.get("model"), site, fallback)
        return await create(*args, **dict(kw, model=fallback))


def check_models():
    """Deprecation signal: warn once per process for configured models that Token Factory no longer lists."""
    global _checked
    with _lock:
        if _checked or os.environ.get("NEMOTRON_MOVE_CHECK", "1") == "0":
            return
        _checked = True
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.tokenfactory.nebius.com/v1").rstrip("/")
    req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            listed = {m.get("id") for m in json.load(r).get("data", [])}
    except Exception as e:
        log.warning("nemotron_move: could not list models at %s/models (%s); deprecation check skipped", base, e)
        return
    wanted = {s[k] for s in SITES.values() for k in ("model", "fallback") if s.get(k)}
    for m in sorted(wanted - listed):
        log.warning("nemotron_move: %s is not listed by %s/models; it may be retired or renamed", m, base)
