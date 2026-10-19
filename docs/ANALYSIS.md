# Analysis — Full-Dataset Run

Results measured from the generated outputs for the complete `data/order_events.csv`; these are observed results, not estimates.

| Metric | Count |
|---|---:|
| Input records | 181,775 |
| Distinct nonempty orders / summary rows | 30,840 |
| Accepted events (`sum(valid_events)`) | 139,647 |
| Anomaly rows | 42,128 (40,561 with an order ID; 1,567 with empty `order_id`) |

**Anomalies by type:** `INVALID_TRANSITION` 13,399; `DUPLICATE_EVENT` 9,675; `UNKNOWN_EVENT` 5,338; `EVENT_ID_CONFLICT` 3,717; `MISSING_TIMESTAMP` 3,066; `INVALID_TIMESTAMP` 2,969; `MISSING_EVENT_ID` 2,397; `MISSING_ORDER_ID` 1,567.

**Reconciliation:** 181,775 input records = 139,647 accepted events + 42,128 anomaly rows. Per-order `anomaly_count` sums to 40,561; empty-order anomalies are reported but not attributed to a summary.

## Edge cases

1. **Identity before validation.** The 9,675 duplicates and 3,717 conflicts are classified before timestamp validation. Only the row with the smallest trimmed `row_id` is canonical, so classification is independent of CSV row order.
2. **Invalid transitions do not stop replay.** Of 13,399 invalid transitions, each is recorded without changing the current state; later events are still evaluated, and accepted events in a valid prefix still count.
3. **Rejected-only orders remain visible.** 405 orders have `UNKNOWN`, zero valid events, empty timestamps, and a nonzero anomaly count. Every nonempty `order_id` in the input must have a summary row, even if all its rows are rejected.

## Engineering tradeoff

**Strict timestamps instead of permissive coercion.** Only the three specified full-second UTC forms are accepted. Unsupported offsets or fractional seconds are rejected rather than guessed because `event_time` determines chronology and coercion could silently reorder events. Empty or duplicate `row_id` values also abort the run instead of inventing an anomaly label.

The full run took approximately 3.0 seconds on Python 3.12.6/Windows in one local measurement; this is not a benchmark or performance guarantee.
