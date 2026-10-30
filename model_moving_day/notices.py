"""Retirement notices: Tavily finds the provider's own deprecation notice for each retiring model, the code checks it.

For every retiring model family in scan.RETIRING, at run time:
  1. Tavily search, limited to the provider's domains, locates the notice page.
  2. Tavily extract reads the pages search found. Copies can be older than the page (2026-10-30: search's indexed copy
     and the extract of the bare URL both ended at the 2026-09-11 entry, while the extracts of the same URL with a
     #fragment returned the 2026-10-14 entry), so every extracted copy is checked and its newest section is recorded.
  3. The code, not a model, decides: the page's host must be on OFFICIAL_HOSTS (community.openai.com is not), a table
     row must name one of the family's exact snapshot IDs in backticks, and the row's shutdown date must parse. The row,
     its section heading and the section's first sentence are kept as the quote. A family is "confirmed" only when the
     date equals the one the census and the scan assume (EXPECTED_SHUTDOWN); a different date is reported, never
     replaced. Rows in the same section that name IDs scan.RETIRING does not know are listed as "also on the list".

Transports, as in tf_client: live (POST https://api.tavily.com/{search,extract}) when TAVILY_API_KEY is set, every
answer recorded under evidence/notices/recordings/; replay otherwise, from those recordings (no key, no network). A
request with no recording is an error, not an empty answer.

Credits: each live call reserves its worst-case cost (search basic 1 / advanced 2, extract 1 per 5 URLs) in
evidence/notices/credits.jsonl and is refused past TAVILY_CREDIT_CAP (default 40). The key is read from the
environment only and never written anywhere.

    mmd notices [--out DIR] [--json] [--replay]
"""
import datetime, hashlib, json, os, re, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import urlparse

from model_moving_day import scan

ROOT = Path(__file__).resolve().parents[1]
NOTICES = ROOT / "evidence" / "notices"
RECORDINGS = NOTICES / "recordings"
CREDITS = NOTICES / "credits.jsonl"
BASE_URL = os.environ.get("TAVILY_BASE_URL", "https://api.tavily.com")
PROVIDER = "OpenAI"
SEARCH_DOMAINS = ["developers.openai.com", "platform.openai.com", "openai.com"]
OFFICIAL_HOSTS = {"developers.openai.com", "platform.openai.com", "openai.com", "help.openai.com"}
EXPECTED_SHUTDOWN = "2027-01-14"  # census/MANIFEST.json baseline.why and scan.py's docstring
MAX_EXTRACT_URLS = 5
RETRY_STATUS = {429, 432, 433, 434, 500, 502, 503}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})|([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})")


class NoticeError(RuntimeError):
    pass


class ReplayMissing(NoticeError):
    pass


class CreditCapReached(NoticeError):
    pass


def families():
    """family name -> sorted snapshot/bare IDs, from scan.RETIRING (the scan's own list)."""
    out = {}
    for i, (fam, _kind) in scan.RETIRING.items():
        out.setdefault(fam, []).append(i)
    return {f: sorted(ids) for f, ids in sorted(out.items())}


def bare_id(family):
    return next(i for i, (f, k) in scan.RETIRING.items() if f == family and k == "bare")


def search_body(family):
    return {"query": f"{PROVIDER} {bare_id(family)} model deprecation shutdown date API", "search_depth": "advanced",
            "max_results": 5, "include_domains": SEARCH_DOMAINS}


def extract_body(urls):
    return {"urls": sorted(urls), "format": "markdown"}


def request_key(endpoint, body):
    canon = json.dumps({"endpoint": endpoint, "body": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:20]


def credits_for(endpoint, body):
    """Worst-case credits of one call as docs.tavily.com prices them (read 2026-10-30)."""
    if endpoint == "search":
        return 2 if body.get("search_depth") == "advanced" else 1
    per = 2 if body.get("extract_depth") == "advanced" else 1
    return per * max(1, -(-len(body.get("urls") or []) // 5))


def parse_date(text):
    """First date in text as YYYY-MM-DD ('2027-01-14', 'January 14, 2027', 'Jan 14, 2027'), else None."""
    for m in _DATE.finditer(text or ""):
        try:
            if m.group(1):
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            mon = MONTHS.get(m.group(4).lower())
            if mon:
                return datetime.date(int(m.group(6)), mon, int(m.group(5))).isoformat()
        except ValueError:
            continue
    return None


def official(url):
    return (urlparse(url).hostname or "").lower() in OFFICIAL_HOSTS


def _sections(markdown):
    """(heading, first sentence, table rows) per '### ' section of a page."""
    out, head, lines = [], None, []
    for line in (markdown or "").splitlines() + ["### "]:
        if line.startswith("### "):
            if head is not None:
                body = [x for x in lines if x.strip()]
                sentence = next((x.strip() for x in body if not x.lstrip().startswith("|")), "")
                rows = [x.strip() for x in body if x.lstrip().startswith("|") and not re.match(r"^\|[\s|:-]+\|$", x.strip())]
                out.append((head, sentence, rows))
            head, lines = line[4:].strip(), []
        else:
            lines.append(line)
    return out


def _cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


def find_row(markdown, ids):
    """The first table row whose model column (the second cell) names one of `ids` as an exact backticked token and
    whose first cell is a date: dict(heading, announced, sentence, row, shutdown, ids_in_row), else None. Only the
    model column counts: a retiring ID that appears as another model's replacement is not its own notice."""
    want = set(ids)
    for head, sentence, rows in _sections(markdown):
        for row in rows:
            cells = _cells(row)
            ticks = set(re.findall(r"`([^`]+)`", cells[1] if len(cells) > 1 else ""))
            if ticks & want:
                shutdown = parse_date(cells[0])
                if shutdown:
                    return {"heading": head, "announced": parse_date(head), "sentence": sentence, "row": row,
                            "shutdown": shutdown, "ids_in_row": sorted(ticks & want)}
    return None


def newest_section(markdown):
    """The latest 'YYYY-MM-DD' among the page's '### ' headings: how fresh the copy Tavily returned is."""
    dates = [d for d in (parse_date(h) for h, _s, _r in _sections(markdown)) if d]
    return max(dates) if dates else None


def also_listed(markdown, heading, known):
    """Rows of the section `heading` that name IDs not in `known`: list of (shutdown, ids)."""
    out = []
    for head, _s, rows in _sections(markdown):
        if head != heading:
            continue
        for row in rows:
            cells = _cells(row)
            ids = [t for t in re.findall(r"`([^`]+)`", " | ".join(cells[1:2])) if t not in known]
            if ids and parse_date(cells[0]):
                out.append({"shutdown": parse_date(cells[0]), "ids": ids})
    return out


def verify(family, ids, pages):
    """pages: list of dict(url, raw_content) from Tavily extract. Returns the family's notice check."""
    res = {"family": family, "ids": ids, "confirmed": False, "expected_shutdown": EXPECTED_SHUTDOWN}
    rejected = [p["url"] for p in pages if not official(p["url"])]
    for p in pages:
        if not official(p["url"]):
            continue
        hit = find_row(p.get("raw_content") or "", ids)
        if hit:
            res.update(url=p["url"], **hit)
            res["confirmed"] = hit["shutdown"] == EXPECTED_SHUTDOWN
            if not res["confirmed"]:
                res["reason"] = f"the notice says {hit['shutdown']}, the census assumes {EXPECTED_SHUTDOWN}"
            break
    else:
        res["reason"] = "no official page among the extracted ones names these IDs in a dated row"
    if rejected:
        res["rejected_hosts"] = sorted({urlparse(u).hostname for u in rejected})
    return res


class CreditLedger:
    def __init__(self, path=CREDITS, cap=None):
        self.path = Path(path)
        self.cap = cap if cap is not None else int(os.environ.get("TAVILY_CREDIT_CAP", "40"))

    def spent(self):
        if not self.path.exists():
            return 0
        return sum(int(json.loads(x).get("credits", 0)) for x in self.path.read_text().splitlines() if x.strip())

    def reserve(self, credits, note):
        total = self.spent()
        if total + credits > self.cap:
            raise CreditCapReached(f"Tavily credits {total} + {credits} would pass the cap of {self.cap} "
                                   "(TAVILY_CREDIT_CAP); stopped before the call")
        self._write(credits, note)

    def settle(self, reserved, actual, note):
        if actual is not None and actual != reserved:
            self._write(actual - reserved, f"settle {note}")

    def _write(self, credits, note):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps({"at": _now(), "credits": credits, "note": note}) + "\n")


class LiveTransport:
    live = True

    def __init__(self, api_key, ledger=None, attempts=3, timeout=120, post=None):
        self.api_key, self.ledger, self.attempts, self.timeout = api_key, ledger or CreditLedger(), attempts, timeout
        self.post = post or self._post

    def _post(self, endpoint, body):
        req = urllib.request.Request(f"{BASE_URL}/{endpoint}", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.api_key}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read()[:300].decode(errors="replace")}

    def answer(self, endpoint, body):
        cost = credits_for(endpoint, body)
        self.ledger.reserve(cost, endpoint)
        last = ""
        for attempt in range(self.attempts):
            try:
                status, raw = self.post(endpoint, {**body, "include_usage": True})
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(2 ** attempt)
                continue
            if status in RETRY_STATUS and attempt < self.attempts - 1:
                last = f"HTTP {status}"
                time.sleep(2 ** attempt)
                continue
            if status != 200:
                raise NoticeError(f"Tavily {endpoint}: HTTP {status} {str(raw)[:200]}")
            self.ledger.settle(cost, (raw.get("usage") or {}).get("credits"), endpoint)
            return raw
        raise NoticeError(f"Tavily {endpoint}: no answer after {self.attempts} attempts ({last})")


class ReplayTransport:
    live = False

    def __init__(self, directory=RECORDINGS):
        self.directory = Path(directory)

    def path(self, endpoint, body):
        return self.directory / f"{endpoint}-{request_key(endpoint, body)}.json"

    def answer(self, endpoint, body):
        p = self.path(endpoint, body)
        if not p.exists():
            raise ReplayMissing(f"no Tavily recording {p.name}; run once with TAVILY_API_KEY set")
        return json.loads(p.read_text())["response"]


class Tavily:
    def __init__(self, transport=None, record_to=RECORDINGS):
        self.transport = transport or default_transport()
        self.record_to = Path(record_to) if record_to else None
        self.calls = []

    @property
    def live(self):
        return self.transport.live

    def call(self, endpoint, body, label):
        started = time.monotonic()
        raw = self.transport.answer(endpoint, body)
        key = request_key(endpoint, body)
        if self.live and self.record_to:
            self.record_to.mkdir(parents=True, exist_ok=True)
            (self.record_to / f"{endpoint}-{key}.json").write_text(
                json.dumps({"recorded_at": _now(), "endpoint": f"{BASE_URL}/{endpoint}", "request": body,
                            "response": raw}, indent=1, ensure_ascii=False) + "\n")
        self.calls.append({"endpoint": endpoint, "label": label, "recording": f"{endpoint}-{key}.json", "live": self.live,
                           "request_id": raw.get("request_id", ""), "credits": (raw.get("usage") or {}).get("credits"),
                           "results": len(raw.get("results") or []), "seconds": round(time.monotonic() - started, 2)})
        return raw


def default_transport(replay=False):
    key = os.environ.get("TAVILY_API_KEY", "")
    return LiveTransport(key) if key and not replay else ReplayTransport()


def check(client=None):
    """Run the check for every family in scan.RETIRING. Returns the notices dict (written by `write`)."""
    client = client or Tavily()
    fams = families()
    found = {}
    for fam in fams:
        raw = client.call("search", search_body(fam), f"search {fam}")
        found[fam] = [{"url": r["url"], "title": r.get("title", ""), "score": r.get("score"),
                       "official": official(r["url"])} for r in raw.get("results") or []]
    urls = []
    for fam in fams:
        for r in found[fam]:
            if r["official"] and r["url"] not in urls:
                urls.append(r["url"])
    dropped = urls[MAX_EXTRACT_URLS:]
    pages = []
    if urls:
        raw = client.call("extract", extract_body(urls[:MAX_EXTRACT_URLS]), "extract official pages")
        pages = [{"url": p["url"], "raw_content": p.get("raw_content") or ""} for p in raw.get("results") or []]
    # off-domain results are verified too, so the host check is what rejects them, visibly
    offsite = [{"url": r["url"], "raw_content": ""} for fam in fams for r in found[fam] if not r["official"]]
    results = [{**verify(fam, ids, pages + offsite), "search_results": found[fam]} for fam, ids in fams.items()]
    known = set(scan.RETIRING)
    extra, seen = [], set()
    for r in results:
        if r.get("heading") and r["heading"] not in seen:
            seen.add(r["heading"])
            page = next(p for p in pages if p["url"] == r["url"])
            extra += also_listed(page["raw_content"], r["heading"], known)
    return {"schema": 1, "provider": PROVIDER, "checked_at": _now(), "live": client.live,
            "expected_shutdown": EXPECTED_SHUTDOWN, "search_domains": SEARCH_DOMAINS,
            "official_hosts": sorted(OFFICIAL_HOSTS),
            "extracted": [{"url": p["url"], "chars": len(p["raw_content"]), "newest_section": newest_section(p["raw_content"])}
                          for p in pages],
            "extract_dropped": dropped, "confirmed": sum(r["confirmed"] for r in results), "families": len(results),
            "results": results, "also_on_the_list": extra, "calls": client.calls,
            "credits": sum(c["credits"] or 0 for c in client.calls)}


def summary(n):
    lines = [f"Retirement notices ({n['provider']}), {'live Tavily calls' if n['live'] else 'replayed Tavily recordings'}"
             f" at {n['checked_at']}: {n['confirmed']} of {n['families']} families confirmed for {n['expected_shutdown']}"]
    for r in n["results"]:
        mark = "confirmed" if r["confirmed"] else "NOT confirmed"
        lines.append(f"  {r['family']}: {mark}" + (f", {r['shutdown']} on {r['url']}" if r.get("shutdown") else "")
                     + (f" ({r['reason']})" if r.get("reason") else ""))
        if r.get("row"):
            lines.append(f"    {r['heading']}")
            lines.append(f"    {r['row']}")
        if r.get("rejected_hosts"):
            lines.append(f"    off the official hosts, not used: {', '.join(r['rejected_hosts'])}")
    lines.append("  extracted copies (newest section): " + ", ".join(f"{p['url']} ({p['newest_section']})"
                                                                for p in n["extracted"]))
    for e in n["also_on_the_list"]:
        lines.append(f"  also on the list, not in the scan's table: {', '.join(e['ids'])} ({e['shutdown']})")
    lines.append(f"  Tavily calls: {len(n['calls'])}, credits: {n['credits']}; request ids: "
                 + ", ".join(c["request_id"] for c in n["calls"] if c["request_id"]))
    return "\n".join(lines)


def write(n, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "notices.json").write_text(json.dumps(n, indent=1, ensure_ascii=False) + "\n")
    (out / "NOTICES.md").write_text("# Retirement notices\n\nWritten by `mmd notices` (model_moving_day/notices.py)."
                                    "\n\n```\n" + summary(n) + "\n```\n")
    return out


def for_ids(n, ids):
    """The notice checks that cover the given retiring IDs (as `mmd scan` finds them)."""
    fams = {scan.RETIRING[i][0] for i in ids if i in scan.RETIRING}
    return [r for r in n["results"] if r["family"] in fams]


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
