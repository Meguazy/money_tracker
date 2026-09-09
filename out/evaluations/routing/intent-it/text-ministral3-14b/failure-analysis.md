# routing/intent-it — Ministral 3 14B final Step 3 failure analysis

- Run: `ROU-20260909-102630-10cb13f4`, dataset `routing-intent-it-v1.2`
  1.2.0 (206 cases).
- Outcome: 193 passed, 13 failed, 0 errors.
- Macro F1: 0.9469 against at least 0.95, fail.
- Warm per-case suite p95: 1,646 ms over 205 measured cases.

All 13 failures are model classifications. Four affect category follow-up
phrasing, three concern world-knowledge or non-owner spending, and the remainder
span expense, refund, advice, negation, meta, and terse inputs. Deterministic
pre-routing remains 16/16; no read-only input reaches a write route and no write
input is treated as read-only. Ambiguous clarification narrowly passes at 52/53
= 0.9811. The model therefore misses the accuracy gate without breaching the
two directional safety gates.
