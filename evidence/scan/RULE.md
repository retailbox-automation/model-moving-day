# Scan rule: which popular Python repositories still name a model that OpenAI removes on January 14, 2027

Written and pushed before the scan. The commit that adds this file is the pre-registration; its time and SHA are
recorded in `evidence/scan/<run date>/SCAN.md`. Anything this file does not fix in advance is reported as a deviation
there, not changed silently.

This is a static scan of public source code. It reads files; it does not install, import or run any code of the
scanned repositories, and it opens no issues or pull requests on them. It uses no model and no API key.

## What counts as a retiring model

From OpenAI's deprecations page as read on 2026-10-28 (`evidence/platform/openai-deprecations-check-2026-10-28.md`):
removal from the API on January 14, 2027, replacement `gpt-6-luna`.

| Family | IDs counted |
|---|---|
| GPT-4o | `gpt-4o`, `gpt-4o-2024-08-06`, `gpt-4o-2024-11-20` |
| GPT-4o mini | `gpt-4o-mini`, `gpt-4o-mini-2024-07-18` |
| GPT-4.1 | `gpt-4.1`, `gpt-4.1-2025-04-14` |
| GPT-4.1 mini | `gpt-4.1-mini`, `gpt-4.1-mini-2025-04-14` |

- The bare names (`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `gpt-4.1-mini`) are counted because every snapshot the page lists
  for them is removed on that date. Results report bare names and dated snapshots in separate columns.
- An ID also counts with one of these provider prefixes: `openai/`, `azure/`, `openrouter/openai/`.
- Matching is exact on the whole string, case-sensitive. `gpt-4o-audio`, `gpt-4o-mini-tts`, `gpt-4o-transcribe`,
  `gpt-4o-realtime-*`, `gpt-4o-search-preview*`, `gpt-4.1-nano` and other models are NOT counted: they are different
  rows of the page or not on it.
- `gpt-4o-2024-05-13` (already removed on October 23, 2026) is reported in its own column and is not in the headline.

## Which repositories

1. Candidates, one query, run once at the start of the scan:
   `gh search repos openai --language=python --sort=stars --order=desc --archived=false --limit 200 --json fullName,stargazersCount,isFork,isArchived,pushedAt,defaultBranch,license,url`
   The raw answer is saved as `candidates.json`.
2. Dropped before cloning, each with its reason in `repos.json`: forks; archived repositories; repositories with no
   push in the 180 days before the query.
3. The rest are taken in the order of the query (most stars first): shallow clone of the default branch
   (`git clone --depth 1`, 300 s timeout). A repository is **kept** if the call-site finder of this project
   (`model_moving_day/find.py`, `scan()`, unchanged) finds at least one LLM call site in its non-test source.
   A repository with no call site is dropped with that reason; a failed clone is dropped and counted as an error.
4. Stop when 50 repositories are kept. If the 200 candidates run out first, the scan reports the number it has; it does
   not widen the query.

## What is measured in each kept repository

Python files are parsed with `ast`; string literals are `ast.Constant` strings (f-strings and concatenations are not
followed). A file that does not parse is counted as an error and skipped.

| Field | Meaning |
|---|---|
| `call_sites` | LLM call sites in non-test source, by `find.scan()` |
| `sites_literal_model` | of those, sites whose `model=` is a literal or a module constant |
| `sites_model_elsewhere` | of those, sites whose model comes from a variable, argument, config or environment |
| `source_ids` | retiring IDs in non-test source `.py` files (file, line, ID) |
| `test_ids` | retiring IDs in test `.py` files (file, line, ID) |
| `test_data_ids` | retiring IDs in non-Python files under test directories (`.yaml`, `.yml`, `.json`, `.toml`), by a whole-word text match; reported, not in the headline |
| `already_removed` | `gpt-4o-2024-05-13` anywhere in `.py` files |
| `errors` | clone, read and parse errors |

A **test file** is a `.py` file under a directory named `tests`, `test` or `testing`, or named `test_*.py`, `*_test.py`
or `conftest.py`. **Non-test source** is what `find.scan()` reads (it also leaves out `docs`, `examples`, `notebooks`,
`scripts`, `cookbook`, `benchmarks`, `evals` and similar); files outside both are not counted.

## Headline numbers, fixed now

- **T**: kept repositories with at least one retiring ID in a test `.py` file.
- **S**: kept repositories with at least one retiring ID in non-test source.
- **U**: kept repositories with either.
- Each is reported as "X of K", where K is the number of kept repositories (50 unless the candidates ran out), next to
  the error counts. No other number from the scan is used as a headline.

What these numbers do not say: that a repository will break (a model name can be overridden by configuration), or
that its tests can decide a replacement model. That second question is what the census answers, call site by call
site, for three repositories.

## Controls

- Before the run, `tests/test_scan.py` must pass, including a synthetic fixture repository with known IDs in source,
  tests, test data and a non-counted near-miss (`gpt-4o-mini-tts`): every planted ID found, the near-miss not found.
- In the run, the same fixture is scanned first and the run stops if it does not give its known counts.

## Output

`evidence/scan/<run date>/`: `candidates.json`, `repos.json` (every candidate with kept/dropped and the reason, the
commit SHA scanned), `results.json` (per kept repository, all fields above), `SCAN.md` (the headline, the error counts,
the code commit that ran, and five repositories checked by hand against GitHub).
