# Analysis — Full-Dataset Run

This note summarizes the observed results of running the engine on
`data/order_events.csv` and the behavior behind the most important categories.
All figures below were measured from the generated `output/` files and verified
independently; they are observations, not estimates.

## Observed results

| Metric | Value |
|---|---|
| Input records processed | 181,775 |
| Distinct nonempty orders / summary rows | 30,840 |
| Accepted events (`sum(valid_events)`) | 139,647 |
| Anomaly rows | 42,128 |
| Anomalies with nonempty `order_id` | 40,561 |
| Anomalies with empty `order_id` | 1,567 |
| Runtime (single local run) | ~2.998 s |
| Input size | 21,547,684 bytes |
| `order_summary.csv` size | 2,324,894 bytes |
| `anomalies.csv` size | 4,072,657 bytes |

Anomaly counts by type:

| Anomaly type | Count |
|---|---:|
| `DUPLICATE_EVENT` | 9,675 |
| `EVENT_ID_CONFLICT` | 3,717 |
| `INVALID_TIMESTAMP` | 2,969 |
| `INVALID_TRANSITION` | 13,399 |
| `MISSING_EVENT_ID` | 2,397 |
| `MISSING_ORDER_ID` | 1,567 |
| `MISSING_TIMESTAMP` | 3,066 |
| `UNKNOWN_EVENT` | 5,338 |

**Interpretation.** Most input rows are usable: 139,647 accepted transitions
across 30,840 orders. The remainder is rejected exactly once each. Invalid
transitions are the largest anomaly class, indicating many records that are
well-formed but violate the permitted lifecycle order; validation and identity
problems (duplicates, conflicts, missing/unknown values) account for the rest.

## Reconciliation

Every input row is accounted for as either an accepted event or a rejected
(anomalous) row:

```text
181,775 input records = 139,647 accepted events + 42,128 anomaly rows
```

The two sides match exactly, with no unmatched records. Of the 42,128 anomalies,
40,561 belong to an order and are reflected in `anomaly_count`; the 1,567 rows
with an empty `order_id` are reported but contribute to no summary count, as
required.

## Edge cases

1. **Duplicate and conflicting event IDs are resolved before validation.**
   9,675 rows were classified as `DUPLICATE_EVENT` and 3,717 as
   `EVENT_ID_CONFLICT`. Because repeated nonempty `event_id` values are grouped
   first, a replayed row with a malformed timestamp is still reported by its
   identity class, and only the canonical row (smallest trimmed `row_id`) enters
   validation. This keeps identity problems from being masked by unrelated field
   errors and makes canonical selection independent of CSV order.

2. **Invalid transitions are isolated, not fatal.** 13,399 events were rejected
   as `INVALID_TRANSITION` (for example, events that arrive after a terminal
   `CANCELLED`/`REFUNDED`, or steps that skip the lifecycle). The engine records
   the anomaly, leaves the current state unchanged, and keeps evaluating later
   events, so a valid prefix still contributes to `valid_events` and to the
   first/last accepted timestamps. Rejected transitions never move the state.

3. **Orders with no accepted event still appear.** 405 of the 30,840 summary
   rows are `UNKNOWN` with `valid_events = 0`, empty timestamps, and a nonzero
   anomaly count. These are orders whose rows were all rejected — for example,
   orders that appear only in duplicate/conflict rows or only in malformed
   records. The summary contract requires coverage of every nonempty
   `order_id` appearing anywhere in the input, so these orders are visible
   rather than silently dropped.

## Design tradeoff

**Strict timestamp validation over permissive coercion.** Only three full-second
ASCII UTC forms are accepted; anything else becomes `INVALID_TIMESTAMP`
(2,969 rows) or `MISSING_TIMESTAMP` (3,066 rows) instead of being guessed or
repaired. The rationale is that `event_time` is the sole chronological key, so a
coerced offset or fractional second could silently reorder a timeline. The cost
is that some values a lenient parser would accept are rejected and must be fixed
upstream. A related decision is that empty or duplicated `row_id` aborts the run
as malformed input rather than adding an anomaly label, preserving one anomaly
per `row_id` and unambiguous accounting.

## Performance caveat

The runtime above (~3.0 s for 181,775 records) is a **single observed local
measurement**, obtained with Python 3.12.6 on Windows. It is not a benchmark and
should not be treated as a performance guarantee; results will vary with
hardware, storage, and input composition.
