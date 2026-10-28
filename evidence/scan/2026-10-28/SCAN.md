# Scan: retiring OpenAI model IDs in popular Python repositories

Rule: `evidence/scan/RULE.md`, commit ccaccc550d4960c6709143210831325d70c30678 2026-10-28T17:47:34-04:00. Code commit that ran: 53b7cc0b (HEAD at the query time, 21:59:00Z commit, pushed before the scan). The script wrote this file at the end and first printed e8480c09 here, the HEAD by then; the commits between them (3457949, b380f49, e8480c0) change only README.md and demo data, not `model_moving_day/` (`git diff --stat 53b7cc0 e8480c0 -- model_moving_day` is empty).
Query time (UTC): 2026-10-28T21:59:36+00:00. Query: `gh search repos openai --language=python --sort=stars --order=desc --archived=false --limit 200 --json fullName,stargazersCount,isFork,isArchived,pushedAt,defaultBranch,license,url`.

## Headline

- **T** (retiring ID in a test `.py` file): 37 of 50
- **S** (retiring ID in non-test source): 34 of 50
- **U** (either): 44 of 50

Errors: 1 failed clones; 1 failed scans; 4 read/parse errors in 1 kept repositories. Candidates: 200.

## Checked by hand against GitHub

| Repository | SHA | What the scan reported | What GitHub shows |
|---|---|---|---|
| microsoft/markitdown | 7482c092d9 | test `packages/markitdown/tests/test_module_misc.py:381` `gpt-4o` | `llm_model="gpt-4o"` on that line |
| Aider-AI/aider | 5dc9490bb3 | test `tests/basic/test_commands.py:927` `gpt-4o` | `vision_model = Model("gpt-4o")` |
| chidiwilliams/buzz | 298dbf0035 | test `tests/plugins/plugin_system_test.py:218` `gpt-4o-mini` | `"model": "gpt-4o-mini",` |
| guardrails-ai/guardrails | 06d0ff2c5f | test `server_ci/tests/test_server.py:31` `gpt-4o-mini` | `model="gpt-4o-mini",` |
| vllm-project/vllm | (default branch) | 0 retiring IDs in tests and source | GitHub code search `"gpt-4o" repo:vllm-project/vllm`: 0 results; the same search on zilliztech/GPTCache finds both files the scan found (control) |

## Deviations from RULE.md

Checked 2026-10-28 ≈22:15Z with `gh api repos/<repo>/contents/<file>?ref=<sha>` (the line the scan reported, at the commit it scanned).

- Not a deviation, but read with care: `openai/openai-python` and `openai/openai-agents-python` are OpenAI's own SDKs and
  count like any other kept repository, as the rule says. Without them T would be 35 of 48.
- Errors, as counted above: `BerriAI/litellm` (rank 15 of the candidates) is dropped as "scan failed": `find.scan()`
  raised `KeyError: 'litellm_acompletion'` on an import of a litellm function under another name. That is a bug in
  `find.py`; the rule fixes `find.py` unchanged for this scan, so it is reported, not fixed here. `livekit/agents` is
  dropped as "clone failed" (git-lfs is not installed on the scanning machine). The next candidates took their places
  by the rule.
- How the code read RULE.md where the rule is not explicit (the code's reading, no other run): a `.py` file that is both
  a test file and read by `find.scan()` (for example `*_test.py`, files under `testing/`) counts as a test file for IDs;
  `already_removed` covers every `.py` file; test files and test data are searched anywhere in the repository except
  `.git`, `.venv`, `venv`, `node_modules`, `site-packages`, `__pycache__`; `sites_model_elsewhere` includes calls with no
  `model=` keyword; whole-word in test data means no letter, digit, `.`, `-` or `/` before the ID and no letter, digit
  or `-` (or `.x`) after it; test-data files over 5 MB are listed in `skipped_large`.
- The output folder is named for the run date, 2026-10-28.
