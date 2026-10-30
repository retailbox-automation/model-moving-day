"""`mmd`: one entry point for the keyless static scan and the census steps.

    mmd scan <path|git-url> [--json] [--notices]  # one repository: call sites and retiring model IDs; no key, no model
    mmd scan-popular --out DIR [--n 50]  # the pre-registered scan of evidence/scan/RULE.md
    mmd notices [--out DIR] [--json] [--replay]  # Tavily finds the provider's retirement notice, the code checks it
    mmd prepare instructor               # anything else goes to `python -m model_moving_day.census` unchanged
"""
import argparse, json, sys, tempfile
from pathlib import Path

from model_moving_day import notices, scan

URL_PREFIXES = ("https://", "http://", "git@", "ssh://", "git://", "file://")


def _ids(title, rows):
    lines = [f"{title}: {len(rows)}"]
    lines += [f"  {r['file']}:{r['line']}  {r['id']}" + (f" ({r['kind']})" if "kind" in r else "") for r in rows]
    return lines


def report(target, r, res):
    lines = [f"repository: {target}",
             f"call sites: {r['call_sites']} (model literal or constant {r['sites_literal_model']}, "
             f"model from elsewhere {r['sites_model_elsewhere']})"]
    lines += [f"  {s.file}:{s.line}  {s.func}  {s.kind}  model={s.model_expr}" for s in res.sites]
    lines += _ids("retiring IDs in source", r["source_ids"]) + _ids("retiring IDs in tests", r["test_ids"])
    lines += _ids("retiring IDs in test data", r["test_data_ids"])
    lines += _ids(f"already removed ({scan.ALREADY_REMOVED})", r["already_removed"])
    lines += [f"errors: {len(r['errors'])}"] + [f"  {e['file']}: {e['error']}" for e in r["errors"]]
    if r["skipped_large"]:
        lines.append(f"test data files over 5 MB skipped: {len(r['skipped_large'])}")
    where = [w for w, k in (("source", "source_ids"), ("tests", "test_ids")) if r[k]]
    lines.append("verdict: " + (f"names a model removed on 2027-01-14 in {' and '.join(where)}" if where
                                else "no retiring model ID in source or tests"))
    return "\n".join(lines)


def notice_lines(checks):
    lines = []
    for c in checks:
        if c["confirmed"]:
            lines.append(f"  {c['family']}: shutdown {c['shutdown']} confirmed on {c['url']}")
            lines.append(f"    {c['row']}")
        else:
            lines.append(f"  {c['family']}: NOT confirmed ({c.get('reason', '')})")
    return lines


def cmd_notices(out, as_json, replay):
    n = notices.check(notices.Tavily(notices.default_transport(replay=replay)))
    if out:
        notices.write(n, out)
    print(json.dumps(n, indent=1, ensure_ascii=False) if as_json else notices.summary(n))
    return 0 if n["confirmed"] == n["families"] else 1


def cmd_scan(target, as_json, with_notices=False):
    with tempfile.TemporaryDirectory(prefix="mmd-scan-") as tmp:
        root, sha = Path(target), None
        if target.startswith(URL_PREFIXES) and not root.exists():
            root = Path(tmp) / "repo"
            sha = scan.git_clone(target, root)
        elif not root.is_dir():
            print(f"mmd scan: not a directory or a git URL: {target}", file=sys.stderr)
            return 2
        r, res = scan.scan_detail(root)
    checks = None
    if with_notices:
        found = {x["id"] for k in ("source_ids", "test_ids", "test_data_ids") for x in r[k]}
        n = notices.check() if found else None
        checks = notices.for_ids(n, found) if n else []
    if as_json:
        print(json.dumps({"repo": target, "sha": sha, **r, **({"notices": checks} if with_notices else {})}, indent=1))
    else:
        text = report(target, r, res) + (f"\nscanned commit: {sha}" if sha else "")
        if with_notices:
            text += f"\nretirement notices ({len(checks)} families, Tavily):\n" + "\n".join(notice_lines(checks))
        print(text)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "notices":
        ap = argparse.ArgumentParser(prog="mmd notices")
        ap.add_argument("--out")
        ap.add_argument("--json", action="store_true")
        ap.add_argument("--replay", action="store_true", help="use the recordings even when TAVILY_API_KEY is set")
        a = ap.parse_args(argv[1:])
        return cmd_notices(a.out, a.json, a.replay)
    if argv and argv[0] in ("scan", "scan-popular"):
        ap = argparse.ArgumentParser(prog=f"mmd {argv[0]}")
        if argv[0] == "scan":
            ap.add_argument("target")
            ap.add_argument("--json", action="store_true")
            ap.add_argument("--notices", action="store_true", help="check each retiring model's notice with Tavily")
            a = ap.parse_args(argv[1:])
            return cmd_scan(a.target, a.json, a.notices)
        ap.add_argument("--out", required=True)
        ap.add_argument("--n", type=int, default=50)
        a = ap.parse_args(argv[1:])
        print(json.dumps(scan.scan_popular(a.out, n=a.n), indent=1))
        return 0
    from model_moving_day import census
    sys.argv = ["mmd", *argv]
    census.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
