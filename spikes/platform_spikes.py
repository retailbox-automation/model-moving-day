"""Platform spikes S1-S6 for the four ladder models. Rules: evidence/spikes/PLAN.md (committed before any run).

    python3 tools/tfenv.py -- python3 spikes/platform_spikes.py run S1
    python3 spikes/platform_spikes.py report          # no API calls; rebuilds evidence/spikes/RESULTS.md

Every call goes through model_moving_day.tf_client (tag "c1"). Two spike-only observers wrap the client's
internals without changing its behaviour: one snapshots the raw response before the client normalises
`arguments: ""`, the other records `x-ratelimit-*` headers and HTTP errors (including retried 429s).
"""
import argparse, concurrent.futures as cf, copy, json, re, statistics, sys, threading, time, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from model_moving_day import tf_client as tf  # noqa: E402

NANO, LIGHT, SUPER, ULTRA = ("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3_5-Lightning",
                             "nvidia/nemotron-3-super-120b-a12b", "nvidia/Nemotron-3-Ultra-550b-a55b")
LADDER = [NANO, LIGHT, SUPER, ULTRA]
SHORT = {NANO: "Nano", LIGHT: "Lightning", SUPER: "Super", ULTRA: "Ultra"}
SAMPLING = {NANO: {"temperature": 0.6, "top_p": 0.95}, LIGHT: {"temperature": 1.0, "top_p": 0.95},
            SUPER: {"temperature": 1.0, "top_p": 0.95}, ULTRA: {"temperature": 1.0, "top_p": 0.95}}
FORMS = {
    "default": {},
    "effort_none": {"reasoning_effort": "none"},
    "ctk_top": {"chat_template_kwargs": {"enable_thinking": False}},
    "ctk_extra": {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
}
RAW = ROOT / "evidence" / "spikes" / "raw"
CAP_C1_SPIKES = 1.00  # PLAN.md stop rule
WORKERS = 8

# --- spike-only observers -------------------------------------------------------------------------------
_local = threading.local()
_orig_parse, _orig_urlopen = tf._parse, tf.urllib.request.urlopen


def _spy_parse(model, raw, *a):
    _local.raw = copy.deepcopy(raw)
    return _orig_parse(model, raw, *a)


def _spy_urlopen(req, timeout=None):
    try:
        resp = _orig_urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        _local.errors.append({"status": e.code, "retry_after": e.headers.get("Retry-After")})
        raise
    _local.headers = {k.lower(): v for k, v in resp.headers.items()
                      if k.lower().startswith(("x-ratelimit", "retry-after"))}
    return resp


tf._parse, tf.urllib.request.urlopen = _spy_parse, _spy_urlopen

_write_lock, _stop = threading.Lock(), threading.Event()
CLIENT = None


def call(spike, label, model, messages, **params):
    if _stop.is_set():
        raise RuntimeError("stopped")
    spent = tf.Ledger().spent()[1].get("c1", 0.0)
    if spent > CAP_C1_SPIKES:
        _stop.set()
        raise RuntimeError(f"stop rule: c1 spent ${spent:.4f} > ${CAP_C1_SPIKES}")
    _local.raw, _local.headers, _local.errors = None, {}, []
    rec = {"spike": spike, "label": label, "model": model, "params": params, "ts": round(time.time(), 3)}
    try:
        r = CLIENT.chat(model, messages, **params)
        rec.update(ok=True, seconds=r.seconds, cost=r.cost, finish=r.finish_reason, content=r.content,
                   reasoning_len=len(r.reasoning), reasoning_tokens=r.reasoning_tokens,
                   tool_calls=r.tool_calls, usage=r.usage, raw=_local.raw)
    except tf.TFError as e:
        rec.update(ok=False, status=e.status, error=str(e)[:600])
        if e.status == 402:
            _stop.set()
    rec.update(headers=_local.headers, http_errors=_local.errors)
    with _write_lock, open(RAW / f"{spike}.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def pool(jobs):
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        futs = [ex.submit(call, *j[:4], **j[4]) for j in jobs]
        out = []
        for fu in cf.as_completed(futs):
            try:
                out.append(fu.result())
            except RuntimeError as e:
                print("stopped:", e)
        return out


# --- tasks ------------------------------------------------------------------------------------------------
ARITH = [{"role": "user", "content": "What is 17 x 23? Reply with the number only."}]
FACT_TOOL = {"type": "function", "function": {
    "name": "write_fact", "description": "Propose a fact for the user's memory graph. Must include a source.",
    "parameters": {"type": "object", "properties": {"subject": {"type": "string"}, "relation": {"type": "string"},
                                                    "object": {"type": "string"}, "source": {"type": "string"}},
                   "required": ["subject", "relation", "object", "source"]}}}
TODAY_TOOL = {"type": "function", "function": {"name": "get_today", "description": "Return today's date.",
                                               "parameters": {"type": "object", "properties": {}}}}
FACT_MSG = [{"role": "system", "content": "You extract durable personal facts from the user's message and call "
                                          "write_fact for each. Use source='user message 2026-10-14'."},
            {"role": "user", "content": "I moved to Austin last week, and my dentist is now Dr. Lee."}]
TODAY_MSG = [{"role": "system", "content": "You are a memory agent. First call get_today. Then call write_fact once "
                                           "for each durable personal fact in the user's message, with "
                                           "source='user message'. You may issue several tool calls in one turn."},
             {"role": "user", "content": "I moved to Austin last week, and my dentist is now Dr. Lee."}]
PEOPLE = [("Maria Lopez", 34, "Denver"), ("Tom Becker", 52, "Austin"), ("Aiko Tanaka", 27, "Seattle"),
          ("Omar Haddad", 41, "Chicago"), ("Lena Fischer", 63, "Boston"), ("Raj Patel", 19, "Phoenix"),
          ("Grace Kim", 38, "Portland"), ("Diego Alvarez", 45, "Miami"), ("Nora Quinn", 71, "Atlanta"),
          ("Sam Okafor", 30, "Dallas"), ("Ivy Chen", 24, "San Diego"), ("Paul Novak", 58, "Detroit"),
          ("Hana Sato", 33, "Honolulu"), ("Ben Carter", 49, "Nashville"), ("Elif Demir", 36, "Orlando"),
          ("Luca Rossi", 62, "Pittsburgh"), ("Mia Wong", 22, "Oakland"), ("Ade Bello", 55, "Houston"),
          ("Zoe Martin", 29, "Tampa"), ("Ken Ito", 67, "Sacramento")]
TEMPLATES = ["{n}, {a}, just moved to {c}.", "Yesterday I met {n} ({a} years old) from {c}.",
             "{n} is {a} and lives in {c}.", "Our new tenant {n}, age {a}, is relocating from {c}."]
SENTENCES = [TEMPLATES[i % 4].format(n=n, a=a, c=c) for i, (n, a, c) in enumerate(PEOPLE)]
PERSON_SCHEMA = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"},
                                                  "city": {"type": "string"}},
                 "required": ["name", "age", "city"], "additionalProperties": False}
PERSON_TOOL = {"type": "function", "function": {"name": "record_person", "description": "Record one person.",
                                                "parameters": PERSON_SCHEMA}}


def person_messages(sentence, schema_in_prompt):
    sys_msg = "Extract the person's name, age and city from the user's sentence."
    if schema_in_prompt:
        sys_msg += (' Reply with a JSON object only, exactly {"name": string, "age": integer, "city": string}.')
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": sentence}]


def run_s1():
    jobs = [("S1", f"{SHORT[m]}|{form}|{k}", m, ARITH, {**SAMPLING[m], "max_tokens": 4096, **FORMS[form]})
            for m in LADDER for form in FORMS for k in range(2)]
    pool(jobs)


def run_s2(nano_off_form):
    jobs = [("S2", f"S2a|Nano|{k}", NANO, FACT_MSG,
             {"tools": [FACT_TOOL], "tool_choice": "auto", "max_tokens": 600, "temperature": 0}) for k in range(5)]
    jobs += [("S2", f"S2b|Nano|{nano_off_form}|{k}", NANO, FACT_MSG,
              {"tools": [FACT_TOOL], "tool_choice": "auto", "max_tokens": 600, **SAMPLING[NANO], **FORMS[nano_off_form]})
             for k in range(5)]
    for m, mt in ((NANO, 10000), (LIGHT, 12000), (SUPER, 12000)):
        extra = {} if m == NANO else {"chat_template_kwargs": {"force_nonempty_content": True}}
        jobs += [("S2", f"S2c|{SHORT[m]}|{k}", m, FACT_MSG,
                  {"tools": [FACT_TOOL], "tool_choice": "auto", "max_tokens": mt, **SAMPLING[m], **extra})
                 for k in range(5)]
    pool(jobs)


def run_s3(off_forms):
    jobs = []
    for m in (NANO, LIGHT):
        off = FORMS[off_forms[m]]
        for i, s in enumerate(SENTENCES):
            base = {**SAMPLING[m], "max_tokens": 1024, **off}
            jobs.append(("S3", f"{SHORT[m]}|json_schema|{i}", m, person_messages(s, False),
                         {**base, "response_format": {"type": "json_schema", "json_schema": {
                             "name": "person", "schema": PERSON_SCHEMA, "strict": True}}}))
            jobs.append(("S3", f"{SHORT[m]}|json_object|{i}", m, person_messages(s, True),
                         {**base, "response_format": {"type": "json_object"}}))
            jobs.append(("S3", f"{SHORT[m]}|named_tool|{i}", m, person_messages(s, False),
                         {**base, "tools": [PERSON_TOOL],
                          "tool_choice": {"type": "function", "function": {"name": "record_person"}}}))
    pool(jobs)


def run_s4():
    ctk = {SUPER: {"force_nonempty_content": True}, ULTRA: {"enable_thinking": True, "force_nonempty_content": True}}
    jobs = [("S4", f"{SHORT[m]}|{k}", m, TODAY_MSG,
             {"tools": [TODAY_TOOL, FACT_TOOL], "tool_choice": "auto", "max_tokens": 8000, **SAMPLING[m],
              "chat_template_kwargs": ctk[m]}) for m in (SUPER, ULTRA) for k in range(20)]
    pool(jobs)


def run_s6(super_off_form, seconds=120):
    msg = [{"role": "user", "content": "Reply with OK."}]
    deadline, n = time.time() + seconds, [0]

    def worker(w):
        while time.time() < deadline and not _stop.is_set():
            n[0] += 1
            call("S6", f"load|Super|w{w}", SUPER, msg, max_tokens=64, **SAMPLING[SUPER], **FORMS[super_off_form])

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    nmsg = [{"role": "user", "content": "Name one prime number between 10 and 20. Reply with the number only."}]
    for n_ in (1, 3):
        call("S6", f"n={n_}|Lightning", LIGHT, nmsg, n=n_, max_tokens=256, **SAMPLING[LIGHT], **FORMS["effort_none"])


# --- report -----------------------------------------------------------------------------------------------
def load(spike):
    p = RAW / f"{spike}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []


def reasoning_field(raw):
    msg = ((raw or {}).get("choices") or [{}])[0].get("message") or {}
    a, b = bool(msg.get("reasoning")), bool(msg.get("reasoning_content"))
    return "both" if a and b else "reasoning" if a else "reasoning_content" if b else "none"


def reasoning_reported(rec):
    return ((rec.get("usage") or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") is not None


def facts(rec):
    found = set()
    for tc in rec.get("tool_calls") or []:
        if tc["function"]["name"] != "write_fact":
            continue
        s = (tc["function"].get("arguments") or "").lower()
        if "austin" in s:
            found.add("austin")
        if "lee" in s or "dentist" in s:
            found.add("dentist")
    return found


def valid_person(obj):
    return (isinstance(obj, dict) and set(obj) == {"name", "age", "city"} and isinstance(obj["name"], str)
            and isinstance(obj["age"], int) and not isinstance(obj["age"], bool) and isinstance(obj["city"], str))


def _median(xs):
    """The median (the mean of the two middle values for an even count); "-" for no values."""
    return statistics.median(xs) if xs else "-"


def report():
    out = ["# Platform spike results", "",
           "Rules fixed in advance: `PLAN.md` (commit d5fb0dd). Raw calls: `raw/S*.jsonl`. Generated by "
           "`spikes/platform_spikes.py report`.", ""]
    allrecs = [r for s in ("S1", "S2", "S3", "S4", "S6") for r in load(s)]
    cost = sum(r.get("cost", 0) for r in allrecs)
    out += [f"Calls: {len(allrecs)}, HTTP errors returned: {sum(not r['ok'] for r in allrecs)}, "
            f"spend from `usage`: ${cost:.4f}.", ""]

    s1 = load("S1")
    if s1:
        out += ["## S1. Reasoning switch", "",
                "Cell = runs passing / runs. Pass = HTTP 200, reasoning tokens 0 or unreported, empty reasoning "
                "field, non-empty content, finish `stop`. `rej` = HTTP 400. In brackets: median reasoning tokens.", "",
                "| Model | " + " | ".join(FORMS) + " | content had 391 |", "|---" * (len(FORMS) + 2) + "|"]
        for m in LADDER:
            cells, right = [], 0
            for form in FORMS:
                rs = [r for r in s1 if r["model"] == m and r["label"].split("|")[1] == form]
                if any(not r["ok"] and r.get("status") == 400 for r in rs):
                    cells.append("rej")
                    continue
                ok = [r for r in rs if r["ok"] and (r.get("reasoning_tokens") or 0) == 0 and r["reasoning_len"] == 0
                      and r["content"].strip() and r["finish"] == "stop"]
                rt = [r.get("reasoning_tokens") or 0 for r in rs if r["ok"]]
                med = _median(rt)
                cells.append(f"{len(ok)}/{len(rs)} ({med}){' ✅' if rs and len(ok) == len(rs) else ''}")
                right += sum("391" in (r.get("content") or "") for r in rs if r["ok"])
            n = sum(1 for r in s1 if r["model"] == m)
            out.append(f"| {SHORT[m]} | " + " | ".join(cells) + f" | {right}/{n} |")
        out.append("")

    s2 = load("S2")
    if s2:
        out += ["## S2. The 14.10 fact task", "", "| Part | Model | Runs | Empty runs | Runs with a parsable write_fact "
                "| finish=length | Facts /10 | Median s | Median completion tokens |", "|---" * 9 + "|"]
        groups = {}
        for r in s2:
            parts = r["label"].split("|")
            groups.setdefault((parts[0], parts[1]), []).append(r)
        for (part, mname), rs in sorted(groups.items()):
            ok = [r for r in rs if r["ok"]]
            empty = sum(1 for r in ok if not r["tool_calls"] and not r["content"].strip())
            withcall = sum(1 for r in ok if any(tc["function"]["name"] == "write_fact" for tc in r["tool_calls"]))
            length = sum(1 for r in ok if r["finish"] == "length")
            nfacts = sum(len(facts(r)) for r in ok)
            secs = sorted(r["seconds"] for r in ok)
            ctoks = sorted((r["usage"] or {}).get("completion_tokens", 0) for r in ok)
            out.append(f"| {part} | {mname} | {len(rs)} ({len(rs) - len(ok)} errors) | {empty} | {withcall} | {length} | "
                       f"{nfacts}/{2 * len(rs)} | {_median(secs)} | {_median(ctoks)} |")
        out.append("")

    s3 = load("S3")
    if s3:
        out += ["## S3. Structured output", "", "| Model | Method | Valid /20 | Values correct /20 | HTTP errors |",
                "|---|---|---|---|---|"]
        for m in (NANO, LIGHT):
            for meth in ("json_schema", "json_object", "named_tool"):
                rs = [r for r in s3 if r["model"] == m and r["label"].split("|")[1] == meth]
                valid = correct = 0
                for r in rs:
                    if not r["ok"]:
                        continue
                    i = int(r["label"].split("|")[2])
                    try:
                        if meth == "named_tool":
                            obj = json.loads(r["tool_calls"][0]["function"]["arguments"]) if r["tool_calls"] else None
                        else:
                            obj = json.loads(r["content"])
                    except (json.JSONDecodeError, KeyError, IndexError):
                        obj = None
                    if valid_person(obj):
                        valid += 1
                        name, age, city = PEOPLE[i]
                        correct += (obj["name"] == name and obj["age"] == age and obj["city"] == city)
                errs = sum(1 for r in rs if not r["ok"])
                err_codes = sorted({r.get("status") for r in rs if not r["ok"]}, key=str)
                out.append(f"| {SHORT[m]} | {meth} | {valid}/{len(rs)}{' ✅' if valid >= 19 else ''} | {correct}/{len(rs)} | "
                           f"{errs}{' ' + str(err_codes) if errs else ''} |")
        out.append("")

    s4 = load("S4")
    if s4:
        out += ["## S4. Tool calling on Super and Ultra", "", "| Model | Parsed /20 | Calls with get_today | "
                "Raw `arguments: \"\"` | Median s | Median completion tokens | Median reasoning tokens |",
                "|---|---|---|---|---|---|---|"]
        for m in (SUPER, ULTRA):
            rs = [r for r in s4 if r["model"] == m]
            parsed = today = empty_args = 0
            for r in rs:
                if not r["ok"] or not r["tool_calls"]:
                    continue
                try:
                    [json.loads(tc["function"]["arguments"]) for tc in r["tool_calls"]]
                    parsed += 1
                except json.JSONDecodeError:
                    pass
                today += any(tc["function"]["name"] == "get_today" for tc in r["tool_calls"])
                raw_tcs = (((r.get("raw") or {}).get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
                empty_args += sum(1 for tc in raw_tcs if not (tc.get("function") or {}).get("arguments"))
            ok = [r for r in rs if r["ok"]]
            med = _median
            out.append(f"| {SHORT[m]} | {parsed}/{len(rs)}{' ✅' if parsed >= 19 else ''} | {today} | {empty_args} | "
                       f"{med([r['seconds'] for r in ok])} | {med([(r['usage'] or {}).get('completion_tokens', 0) for r in ok])} | "
                       f"{med([r.get('reasoning_tokens') or 0 for r in ok])} |")
        out.append("")

    if allrecs:
        out += ["## S5. Where reasoning text arrives", "",
                "| Model | Responses | reasoning | reasoning_content | both | none | reasoning_tokens reported |",
                "|---|---|---|---|---|---|---|"]
        for m in LADDER:
            rs = [r for r in allrecs if r["model"] == m and r["ok"]]
            c = {k: sum(1 for r in rs if reasoning_field(r.get("raw")) == k) for k in ("reasoning", "reasoning_content", "both", "none")}
            out.append(f"| {SHORT[m]} | {len(rs)} | {c['reasoning']} | {c['reasoning_content']} | {c['both']} | {c['none']} | "
                       f"{sum(reasoning_reported(r) for r in rs)}/{len(rs)} |")
        out.append("")

    s6 = load("S6")
    if s6:
        load_recs = [r for r in s6 if r["label"].startswith("load")]
        n429 = sum(1 for r in load_recs for e in r["http_errors"] if e["status"] == 429)
        if load_recs:
            span = max(r["ts"] for r in load_recs) - min(r["ts"] for r in load_recs) or 1
            hdr = next((r["headers"] for r in reversed(load_recs) if r["headers"]), {})
            out += ["## S6. Limits, headers, n", "",
                    f"Super, {WORKERS} concurrent, {len(load_recs)} calls over {span:.0f} s = "
                    f"{len(load_recs) / span * 60:.0f} calls/min; HTTP 429 seen: {n429}; "
                    f"other HTTP errors: {sum(1 for r in load_recs for e in r['http_errors'] if e['status'] != 429)}.", "",
                    "Last `x-ratelimit-*` headers seen:", "", "```", json.dumps(hdr, indent=1, sort_keys=True), "```", ""]
        for r in s6:
            if r["label"].startswith("n="):
                u = r.get("usage") or {}
                nch = len(((r.get("raw") or {}).get("choices") or []))
                out.append(f"- `{r['label']}`: prompt_tokens {u.get('prompt_tokens')}, completion_tokens "
                           f"{u.get('completion_tokens')}, choices returned {nch}, cost ${r.get('cost', 0):.6f}")
        out.append("")
    out += verdicts(s1, s2)
    (ROOT / "evidence" / "spikes" / "RESULTS.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


def verdicts(s1, s2):
    """Pass/fail against the PLAN.md rules that need more than one table cell."""
    out = ["## Verdicts against PLAN.md", ""]
    if s1:
        for m in LADDER:
            good = [f for f in FORMS if f != "default" and all(
                r["ok"] and (r.get("reasoning_tokens") or 0) == 0 and r["reasoning_len"] == 0 and r["content"].strip()
                and r["finish"] == "stop" for r in s1 if r["model"] == m and r["label"].split("|")[1] == f)]
            out.append(f"- S1 {SHORT[m]}: reasoning-off forms that pass: {', '.join(good) or 'none'}")
    if s2:
        def grp(prefix):
            return [r for r in s2 if r["label"].startswith(prefix) and r["ok"]]
        a = grp("S2a|Nano")
        empty = sum(1 for r in a if not r["tool_calls"] and not r["content"].strip())
        out.append(f"- S2a empty Nano reproduced: {'yes' if empty >= 3 else 'no'} ({empty}/5 empty)")
        b = grp("S2b|Nano")
        fixed = sum(1 for r in b if any(tc["function"]["name"] == "write_fact" for tc in r["tool_calls"]))
        blen = sum(1 for r in b if r["finish"] == "length")
        out.append(f"- S2b fixed: {'yes' if fixed >= 4 and blen == 0 else 'no'} ({fixed}/5 with write_fact, {blen} length)")
        for name, bar in (("Super", 9), ("Lightning", 8)):
            n = sum(len(facts(r)) for r in grp(f"S2c|{name}"))
            out.append(f"- S2c {name}: {n}/10 facts, bar {bar} -> {'pass' if n >= bar else 'FAIL'}")
    return out + [""]


def fixtures():
    """Pick recorded raw responses of all four models for the S5 unit test (tests/test_tf_recorded.py)."""
    picks = []
    for spike, pred in (("S1", lambda r: r["label"].endswith("|default|0")),
                        ("S1", lambda r: r["label"].endswith("|effort_none|0")),
                        ("S2", lambda r: r["label"] in ("S2a|Nano|0", "S2b|Nano|ctk_top|0", "S2c|Lightning|0",
                                                         "S2c|Super|0", "S2c|Nano|0")),
                        ("S4", lambda r: r["label"] in ("Super|0", "Ultra|0"))):
        picks += [{"spike": spike, "label": r["label"], "model": r["model"], "raw": r["raw"]}
                  for r in load(spike) if r["ok"] and pred(r)]
    path = ROOT / "tests" / "fixtures" / "tf_recorded.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(picks, indent=1) + "\n")
    print(f"{len(picks)} recorded responses -> {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "report", "fixtures"])
    ap.add_argument("spike", nargs="?")
    ap.add_argument("--off", default="", help="reasoning-off form per model, e.g. Nano=effort_none,Lightning=effort_none")
    a = ap.parse_args()
    if a.cmd == "report":
        return report()
    if a.cmd == "fixtures":
        return fixtures()
    global CLIENT
    CLIENT = tf.Client("c1", log=lambda m: print(m, flush=True))
    RAW.mkdir(parents=True, exist_ok=True)
    off = dict(kv.split("=") for kv in a.off.split(",") if kv)
    t0 = time.time()
    {"S1": run_s1, "S2": lambda: run_s2(off["Nano"]), "S3": lambda: run_s3({NANO: off["Nano"], LIGHT: off["Lightning"]}),
     "S4": run_s4, "S6": lambda: run_s6(off["Super"])}[a.spike]()
    print(f"{a.spike} done in {time.time() - t0:.0f} s; ledger: {tf.Ledger().spent()}")


if __name__ == "__main__":
    main()
