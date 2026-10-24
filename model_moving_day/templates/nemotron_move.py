"""Nemotron move helper (added by Model Moving Day, MIT). Off unless NEMOTRON_MOVE=1.

With NEMOTRON_MOVE=1 each moved call site sends the Nemotron model the census chose for it, with the request
parameters the census measured for that model (SITES below), instead of the model the caller named.
NEMOTRON_MODEL_<SITE> overrides one site (and then no fallback is used).

Nested sites: when a moved call runs inside another moved call (for example a validator call that goes through a
library funnel), the outer site's choice stands and the inner site leaves the request alone.

On an answer that means "unknown model" (RETIRED_STATUSES; Token Factory does not reroute retired models) the call is
retried once on the site's fallback, but only when the census measured a fallback cell that passed; each fallback
carries its own parameters. Without a measured fallback the error is raised.

Deprecation signal: with NEMOTRON_MOVE_BASE_URL and NEMOTRON_MOVE_API_KEY set, the first moved call lists
<base>/models and logs a warning for every configured model that is missing (a failed listing is retried after
five minutes). Without both variables the check is skipped with one warning: the helper never sends any other
key anywhere.
"""
import contextvars, json, logging, os, threading, time, urllib.request

log = logging.getLogger("nemotron_move")

# site -> {"model", "params": merged into the call, "drop": keys removed, "min_max_tokens": budget floor,
#          "fallback": the same fields for the measured fallback model, or None}
SITES = {}  # filled in by the migration patch
RETIRED_STATUSES = {404}  # filled in by the migration patch from Token Factory's measured answer to an unknown ID

_active = contextvars.ContextVar("nemotron_move_site", default=None)
_checked, _next_try, _lock = False, 0.0, threading.Lock()
RETRY_LISTING_S = 300


def enabled():
    return os.environ.get("NEMOTRON_MOVE", "").lower() in ("1", "true", "yes")


def _site_env(site):
    return "NEMOTRON_MODEL_" + "".join(c if c.isalnum() else "_" for c in site).upper()


def model_for(site, requested):
    """The model this call site should send: the caller's choice unless the move is enabled."""
    if not enabled() or site not in SITES:
        return requested
    return os.environ.get(_site_env(site)) or SITES[site]["model"]


def _merge(base, extra):
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _apply(kwargs, cfg, model):
    kw = dict(kwargs, model=model)
    for k, v in (cfg.get("params") or {}).items():
        kw[k] = _merge(kw[k], v) if isinstance(v, dict) and isinstance(kw.get(k), dict) else v
    for k in cfg.get("drop") or []:
        kw.pop(k, None)
        if isinstance(kw.get("extra_body"), dict) and k in kw["extra_body"]:
            kw["extra_body"] = {x: y for x, y in kw["extra_body"].items() if x != k}
    floor = cfg.get("min_max_tokens")
    if floor:
        key = "max_completion_tokens" if "max_completion_tokens" in kw and "max_tokens" not in kw else "max_tokens"
        if kw.get(key) is None or kw[key] < floor:
            kw[key] = floor
    return kw


def _plan(site, kwargs):
    """(request for the chosen model, request for the measured fallback or None)."""
    check_models()
    cfg, override = SITES[site], os.environ.get(_site_env(site))
    kw = _apply(kwargs, cfg, override or cfg["model"])
    fb = cfg.get("fallback")
    return kw, (_apply(kwargs, fb, fb["model"]) if fb and not override else None)


def _moving(site):
    return enabled() and site in SITES and _active.get() is None


def _is_retired(exc):
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status in RETIRED_STATUSES


def call(site, create, *args, **kwargs):
    if not _moving(site):
        return create(*args, **kwargs)
    kw, fallback = _plan(site, kwargs)
    token = _active.set(site)
    try:
        try:
            return create(*args, **kw)
        except Exception as e:
            if fallback is None or not _is_retired(e):
                raise
            log.warning("nemotron_move: %s is unknown at %s (%s), retrying on the measured fallback %s",
                        kw["model"], site, type(e).__name__, fallback["model"])
            return create(*args, **fallback)
    finally:
        _active.reset(token)


async def acall(site, create, *args, **kwargs):
    if not _moving(site):
        return await create(*args, **kwargs)
    kw, fallback = _plan(site, kwargs)
    token = _active.set(site)
    try:
        try:
            return await create(*args, **kw)
        except Exception as e:
            if fallback is None or not _is_retired(e):
                raise
            log.warning("nemotron_move: %s is unknown at %s (%s), retrying on the measured fallback %s",
                        kw["model"], site, type(e).__name__, fallback["model"])
            return await create(*args, **fallback)
    finally:
        _active.reset(token)


def check_models():
    """Deprecation signal: warn once per process for configured models the platform no longer lists."""
    global _checked, _next_try
    with _lock:
        if _checked or os.environ.get("NEMOTRON_MOVE_CHECK", "1") == "0" or time.time() < _next_try:
            return
        _next_try = time.time() + RETRY_LISTING_S
    base, key = os.environ.get("NEMOTRON_MOVE_BASE_URL"), os.environ.get("NEMOTRON_MOVE_API_KEY")
    if not base or not key:
        log.warning("nemotron_move: NEMOTRON_MOVE_BASE_URL and NEMOTRON_MOVE_API_KEY are not both set; "
                    "the deprecation check is skipped")
        with _lock:
            _checked = True
        return
    base = base.rstrip("/")
    req = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            listed = {m.get("id") for m in json.load(r).get("data", [])}
    except Exception as e:
        log.warning("nemotron_move: could not list models at %s/models (%s); will retry in %d s", base, e, RETRY_LISTING_S)
        return
    with _lock:
        _checked = True
    wanted = {m for s in SITES.values() for m in (s.get("model"), (s.get("fallback") or {}).get("model")) if m}
    for m in sorted(wanted - listed):
        log.warning("nemotron_move: %s is not listed by %s/models; it may be retired or renamed", m, base)
