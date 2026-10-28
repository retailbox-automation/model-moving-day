#!/usr/bin/env bash
# The recorded re-run of PLAN.md. Run from the repository root of a checkout on branch rerun-2026-10-29, with
# NEBIUS_API_KEY and NEBIUS_PROJECT_ID set; PY is the interpreter with the package's dependencies.
set -euo pipefail
PY=${PY:-python3}
say() { printf '\n\033[1;36m$ %s\033[0m\n' "$*"; }

printf '\033[1mModel Moving Day: re-run of one call site, %s UTC (not part of the census)\033[0m\n' "$(date -u '+%Y-%m-%d %H:%M')"
printf 'site  llm/default_plugins/openai_models.py:1494\nrules K=5 runs per cell, >=4 of 5 pass, Nano -> Lightning -> Super -> Ultra, in Token Factory Sandboxes\n'

say "python -m model_moving_day.census ladder llm"
"$PY" -m model_moving_day.census ladder llm 

RUN=$(ls -t evidence/census/llm/ladder-*.json | head -1 | sed 's/.*ladder-\(.*\)\.json/\1/')
say "python -m model_moving_day.census patch llm --ladder-run $RUN"
"$PY" -m model_moving_day.census patch llm --ladder-run "$RUN"
printf '\n\033[1mdone %s UTC\033[0m\n' "$(date -u '+%H:%M:%S')"
