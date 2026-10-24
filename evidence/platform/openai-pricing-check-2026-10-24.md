# OpenAI price and retirement check for the "before" baseline, 2026-10-24

Read at 2026-10-24T14:36Z with curl (browser user agent). Both URLs redirected to developers.openai.com and returned
HTTP 200:

- https://platform.openai.com/docs/pricing.md -> https://developers.openai.com/api/docs/pricing.md (22,449 bytes)
- https://platform.openai.com/docs/deprecations.md -> https://developers.openai.com/api/docs/deprecations.md (42,211 bytes)

Lines quoted verbatim.

pricing.md, section "Standard pricing data" (prices per 1M tokens; columns: Model, Short context input, Short context
output, ...):

    | gpt-6-luna | $0.10 | $0.50 | $0.01 | $0.125 | $0.25 | $0.20 | $0.75 | $0.02 | $0.25 | $0.50 |

deprecations.md, section "2026-10-14: GPT-4 series model deprecations" (shutdown date, model, recommended
replacement):

    | January 14, 2027 | GPT-4o (`gpt-4o-2024-08-06`, `gpt-4o-2024-11-20`) | `gpt-6-luna`            |
    | January 14, 2027 | GPT-4o mini (`gpt-4o-mini-2024-07-18`)            | `gpt-6-luna`            |
    | January 14, 2027 | GPT-4.1 (`gpt-4.1-2025-04-14`)                    | `gpt-6-luna`            |
    | January 14, 2027 | GPT-4.1 mini (`gpt-4.1-mini-2025-04-14`)          | `gpt-6-luna`            |

Result: the manifest's baseline (gpt-6-luna at $0.10 input / $0.50 output per 1M tokens, the recommended replacement
for the models the targets' tests name, read 2026-10-20) matches the live pages. gpt-6-luna itself was never called.
