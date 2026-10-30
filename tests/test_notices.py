"""Retirement notices: the code's checks on synthetic pages, the transports, and the recorded run of 2026-10-30."""
import json

import pytest

from model_moving_day import cli, notices as N

PAGE = """### 2026-10-14: GPT-4 series model deprecations

On October 14, 2026, we notified developers using certain GPT-4 series models of their deprecation and removal from the API on January 14, 2027.

| Shutdown date    | Model / system                                    | Recommended replacement |
| ---------------- | ------------------------------------------------- | ----------------------- |
| January 14, 2027 | GPT-4o (`gpt-4o-2024-08-06`, `gpt-4o-2024-11-20`) | `gpt-6-luna`            |
| January 14, 2027 | GPT-4o mini (`gpt-4o-mini-2024-07-18`)            | `gpt-6-luna`            |
| January 14, 2027 | GPT-4.1 (`gpt-4.1-2025-04-14`)                    | `gpt-6-luna`            |
| January 14, 2027 | GPT-4.1 mini (`gpt-4.1-mini-2025-04-14`)          | `gpt-6-luna`            |
| January 14, 2027 | GPT-4 Turbo (`gpt-4-turbo-2024-04-09`)            | `gpt-6-luna`            |

### 2024-06-06: GPT-4-32K and Vision Preview models

| Shutdown date | Model | Price | Recommended replacement |
| --- | --- | --- | --- |
| 2025-06-06 | `gpt-4-32k` | $60.00 / 1M input tokens | `gpt-4o` |
"""
OFFICIAL = "https://developers.openai.com/api/docs/deprecations"
FAM = N.families()


def page(md, url=OFFICIAL):
    return {"url": url, "raw_content": md}


def test_families_come_from_the_scan_table():
    assert set(FAM) == {"GPT-4o", "GPT-4o mini", "GPT-4.1", "GPT-4.1 mini"}
    assert "gpt-4o-2024-11-20" in FAM["GPT-4o"] and N.bare_id("GPT-4o mini") == "gpt-4o-mini"


@pytest.mark.parametrize("text,want", [("January 14, 2027", "2027-01-14"), ("Jan 14, 2027", "2027-01-14"),
                                       ("2027-01-14", "2027-01-14"), ("2026-10-14: GPT-4 series", "2026-10-14"),
                                       ("Feb 30, 2027", None), ("soon", None), ("", None)])
def test_parse_date(text, want):
    assert N.parse_date(text) == want


def test_confirmed_with_quote_heading_and_announcement():
    r = N.verify("GPT-4o mini", FAM["GPT-4o mini"], [page(PAGE)])
    assert r["confirmed"] and r["shutdown"] == "2027-01-14" and r["announced"] == "2026-10-14"
    assert r["ids_in_row"] == ["gpt-4o-mini-2024-07-18"] and "`gpt-4o-mini-2024-07-18`" in r["row"]
    assert r["sentence"].startswith("On October 14, 2026") and r["url"] == OFFICIAL


def test_every_family_confirmed_on_the_page():
    assert all(N.verify(f, ids, [page(PAGE)])["confirmed"] for f, ids in FAM.items())


def test_off_domain_page_is_rejected_even_with_the_same_text():
    r = N.verify("GPT-4o", FAM["GPT-4o"], [page(PAGE, "https://community.openai.com/t/sunset/1")])
    assert not r["confirmed"] and r["rejected_hosts"] == ["community.openai.com"] and "shutdown" not in r


def test_lookalike_host_is_not_official():
    assert not N.official("https://developers.openai.com.evil.example/x")
    assert not N.official("https://openai.com-docs.example/x") and N.official("https://openai.com/index/x")


def test_id_as_a_replacement_is_not_its_own_notice():
    only_old = PAGE.split("### 2024-06-06")[1]
    r = N.verify("GPT-4o", FAM["GPT-4o"], [page("### 2024-06-06" + only_old)])
    assert not r["confirmed"] and "shutdown" not in r


def test_other_date_is_reported_not_replaced():
    r = N.verify("GPT-4.1", FAM["GPT-4.1"], [page(PAGE.replace("January 14, 2027 | GPT-4.1 (", "March 3, 2027 | GPT-4.1 ("))])
    assert not r["confirmed"] and r["shutdown"] == "2027-03-03" and "2027-03-03" in r["reason"]


def test_absent_ids_are_not_confirmed():
    r = N.verify("GPT-4.1", FAM["GPT-4.1"], [page(PAGE.replace("gpt-4.1-2025-04-14", "gpt-4.1-2099-01-01"))])
    assert not r["confirmed"] and "no official page" in r["reason"]


def test_stale_copy_first_fresh_copy_second():
    stale = page("### 2026-09-11: Something else\n\n| Date | Model |\n| --- | --- |\n| Dec 1, 2026 | `x` |\n")
    r = N.verify("GPT-4o", FAM["GPT-4o"], [stale, page(PAGE, OFFICIAL + "#fragment")])
    assert r["confirmed"] and r["url"].endswith("#fragment")
    assert N.newest_section(stale["raw_content"]) == "2026-09-11" and N.newest_section(PAGE) == "2026-10-14"


def test_also_on_the_list_names_unknown_ids_of_the_same_section():
    extra = N.also_listed(PAGE, "2026-10-14: GPT-4 series model deprecations", set(N.scan.RETIRING))
    assert extra == [{"shutdown": "2027-01-14", "ids": ["gpt-4-turbo-2024-04-09"]}]


class FakeLive:
    live = True

    def __init__(self, pages):
        self.pages, self.seen = pages, []

    def answer(self, endpoint, body):
        self.seen.append((endpoint, body))
        if endpoint == "search":
            return {"request_id": f"s{len(self.seen)}", "usage": {"credits": 2},
                    "results": [{"url": OFFICIAL, "title": "Deprecations", "score": .9},
                                {"url": "https://community.openai.com/t/1", "title": "forum", "score": .8}]}
        return {"request_id": "e1", "usage": {"credits": 1},
                "results": [{"url": u, "raw_content": self.pages.get(u, "")} for u in body["urls"]]}


def test_check_end_to_end_records_and_replays(tmp_path):
    rec = tmp_path / "rec"
    live = FakeLive({OFFICIAL: PAGE})
    n = N.check(N.Tavily(live, record_to=rec))
    assert n["live"] and n["confirmed"] == n["families"] == 4 and n["credits"] == 9
    assert [e for e, _ in live.seen] == ["search"] * 4 + ["extract"]
    assert live.seen[0][1]["include_domains"] == N.SEARCH_DOMAINS and live.seen[-1][1]["urls"] == [OFFICIAL]
    assert n["extracted"] == [{"url": OFFICIAL, "chars": len(PAGE), "newest_section": "2026-10-14"}]
    assert n["also_on_the_list"] == [{"shutdown": "2027-01-14", "ids": ["gpt-4-turbo-2024-04-09"]}]
    assert len(list(rec.glob("*.json"))) == 5
    again = N.check(N.Tavily(N.ReplayTransport(rec), record_to=None))
    assert not again["live"] and [r["confirmed"] for r in again["results"]] == [r["confirmed"] for r in n["results"]]
    out = N.write(n, tmp_path / "out")
    assert json.loads((out / "notices.json").read_text())["confirmed"] == 4
    assert "4 of 4 families confirmed" in (out / "NOTICES.md").read_text()


def test_check_without_the_row_confirms_nothing(tmp_path):
    n = N.check(N.Tavily(FakeLive({OFFICIAL: "### 2026-09-11: Nothing\n"}), record_to=None))
    assert n["confirmed"] == 0 and all(r["reason"] for r in n["results"])


def test_replay_without_a_recording_is_an_error(tmp_path):
    with pytest.raises(N.ReplayMissing):
        N.ReplayTransport(tmp_path).answer("search", N.search_body("GPT-4o"))


def test_no_key_means_replay(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert isinstance(N.default_transport(), N.ReplayTransport)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert isinstance(N.default_transport(), N.LiveTransport)
    assert isinstance(N.default_transport(replay=True), N.ReplayTransport)


def test_credit_cap_stops_before_the_call(tmp_path):
    calls = []
    live = N.LiveTransport("k", ledger=N.CreditLedger(tmp_path / "c.jsonl", cap=3),
                           post=lambda e, b: calls.append(e) or (200, {"usage": {"credits": 2}, "results": []}))
    live.answer("search", N.search_body("GPT-4o"))
    with pytest.raises(N.CreditCapReached):
        live.answer("search", N.search_body("GPT-4o mini"))
    assert calls == ["search"] and live.ledger.spent() == 2


def test_live_http_error_is_raised_not_swallowed(tmp_path):
    live = N.LiveTransport("k", ledger=N.CreditLedger(tmp_path / "c.jsonl", cap=10), attempts=1,
                           post=lambda e, b: (401, {"error": "unauthorized"}))
    with pytest.raises(N.NoticeError, match="401"):
        live.answer("search", N.search_body("GPT-4o"))


def test_extract_credits():
    assert N.credits_for("extract", {"urls": ["a"] * 5}) == 1 and N.credits_for("extract", {"urls": ["a"] * 6}) == 2
    assert N.credits_for("search", N.search_body("GPT-4o")) == 2


def test_recorded_run_replays_to_four_of_four(monkeypatch):
    """The recordings committed under evidence/notices/recordings/ (live run of 2026-10-30) replay with no key."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    n = N.check(N.Tavily(record_to=None))
    assert not n["live"] and n["confirmed"] == 4 and n["credits"] == 9
    assert {r["heading"] for r in n["results"]} == {"2026-10-14: GPT-4 series model deprecations"}
    assert all(N.official(r["url"]) for r in n["results"])
    assert any(p["newest_section"] == "2026-09-11" for p in n["extracted"])  # the stale copy is in the record
    saved = json.loads((N.NOTICES / "2026-10-30" / "notices.json").read_text())
    assert saved["live"] and saved["confirmed"] == 4
    assert [r["row"] for r in saved["results"]] == [r["row"] for r in n["results"]]


def test_cli_notices_replay(monkeypatch, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert cli.main(["notices"]) == 0
    assert "4 of 4 families confirmed for 2027-01-14" in capsys.readouterr().out


def test_cli_scan_with_notices_on_the_fixture(monkeypatch, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert cli.main(["scan", str(N.scan.FIXTURE), "--notices"]) == 0
    out = capsys.readouterr().out
    assert "retirement notices" in out and "confirmed on https://developers.openai.com/" in out


def test_cli_notices_without_key_or_recordings_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(N, "RECORDINGS", tmp_path)
    monkeypatch.setattr(N.ReplayTransport.__init__, "__defaults__", (tmp_path,))
    assert cli.main(["notices"]) == 2
    assert "recordings live in a clone" in capsys.readouterr().err
