# Ponytail debt

Every deliberate shortcut left in the tree, harvested at the Week 4 audit.
Format: `<file>:<line>, what was simplified. ceiling: the limit. upgrade: the trigger.`

| Where | What was simplified | Ceiling | Upgrade when |
|---|---|---|---|
| `tools.py:207` | Port list read out of RTL source with a regex instead of a real Verilog parser | Fails on non-ANSI port declarations (`module foo(a,b);` styles) | Any generated RTL stops matching the pattern — a `rtl_matches_spec` false positive shows up |
| `tests/check_llm.py:13` | Imports `google.genai._transformers`, a private module, as a structured-output canary | Breaks on any SDK release that renames or drops the private hook | The import stops resolving on a `google-genai` upgrade — swap for a live canary call against the Gemini API |

**2 markers, 0 with no trigger.**

One marker was retired during the audit rather than carried: the single-file LLM
cache (originally `# ponytail: <ceiling>, sqlite if it grows`) grew a per-provider
key ring and a token-usage ledger in Week 1, so the ceiling it named was reached
inside a day. It was removed rather than re-documented against a ceiling that no
longer described the code.

## The check that keeps this honest

`grep -rnE '(#|//) ?ponytail:' .` — a marker with no trigger after the colon is
the rot case, and the audit failed the build if one appeared. Both rows above
name the observable event that retires them, not "when there's time".
