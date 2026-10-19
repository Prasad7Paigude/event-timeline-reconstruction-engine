# Event Timeline Reconstruction Engine

A command-line Python tool that reconstructs e-commerce order timelines from a
messy order-event CSV. It normalizes and validates records, resolves duplicated
and conflicting event IDs, replays each order through a fixed business state
machine, and writes a per-order summary plus an anomaly report. The goal is
correctness, deterministic output, and transparent reasoning — not dashboards or
heavy architecture.

## Requirements and setup

- Python 3.10 or newer.
- Standard library only: no third-party packages, no `requirements.txt`, and no
  installation step.
- No network access, API keys, or external services are used.

## Usage

```bash
python main.py --input data/order_events.csv --output-dir output
```

`--input` may point at any CSV that matches the input schema; the program needs no
code changes or retraining to process a different file. Both options are required.

## Outputs

Both files are always written (headers only when there is no data).

`order_summary.csv` — one row per distinct nonempty `order_id`:

```text
order_id,final_status,valid_events,anomaly_count,first_valid_event_time,last_valid_event_time
```

`final_status` is the last accepted state or `UNKNOWN` when no event was accepted;
`valid_events` counts accepted transitions (including `PLACED`); `anomaly_count`
is the number of anomaly rows attributed to the order; the timestamps are UTC
`YYYY-MM-DDTHH:MM:SSZ` or empty. Rows are sorted by `order_id`.

`anomalies.csv` — one row per rejected input row (at most one per `row_id`):

```text
row_id,order_id,event_id,anomaly_type,details
```

`anomaly_type` is one of `DUPLICATE_EVENT`, `EVENT_ID_CONFLICT`,
`MISSING_ORDER_ID`, `MISSING_EVENT_ID`, `MISSING_TIMESTAMP`, `INVALID_TIMESTAMP`,
`UNKNOWN_EVENT`, `INVALID_TRANSITION`. Rows are sorted by `row_id`.

## Design decisions

- **Normalization.** Every cell is trimmed; empty/whitespace-only cells are
  missing. `event_type` is trimmed and uppercased only (no aliases); `event_id`
  and `order_id` are case-sensitive strings after trimming. `received_at`,
  `source_system`, and `metadata` are informational and never affect identity,
  ordering, or transitions.
- **Duplicate / conflict resolution (before validation).** Repeated nonempty
  `event_id` values are grouped; the canonical row is the one with the
  lexicographically smallest trimmed `row_id`, which stays deterministic if the
  file is shuffled. Each other row is compared on
  `(order_id, normalized event_type, raw event_time)`: equal becomes
  `DUPLICATE_EVENT`, different becomes `EVENT_ID_CONFLICT`. Both are rejected
  before validation. Rows with an empty `event_id` are never grouped.
- **Validation precedence.** Only the first applicable problem is recorded, in
  this order: `MISSING_ORDER_ID`, `MISSING_EVENT_ID`, `MISSING_TIMESTAMP`,
  `INVALID_TIMESTAMP`, `UNKNOWN_EVENT`.
- **Timestamps.** Only three full-second UTC forms are accepted
  (`...Z`, `YYYY-MM-DD HH:MM:SS`, `...+00:00`), each checked by an exact pattern
  and then by real calendar/time validation. Unzoned values are treated as UTC
  and all output is normalized to `...Z`.
- **Chronological replay.** Eligible rows are grouped by `order_id` and sorted by
  `(event_time, event_id, row_id)`. Each order is replayed from `START`; an
  illegal step records `INVALID_TRANSITION`, leaves the state unchanged, and
  evaluation continues. History is never repaired or synthesized.
- **Determinism.** Output is invariant under row permutation: canonical
  selection, tie-breaks, and output ordering are all explicit, and maps are never
  relied on for output order.
- **Input integrity.** A missing/unreadable file, malformed CSV, missing required
  columns, invalid UTF-8, or an empty/duplicated `row_id` fails fast with a clear
  message and a nonzero exit rather than inventing data.

## Tests

```bash
python -m unittest discover -s tests -v
```

34 unit tests cover canonical duplicate/conflict selection, validation
precedence and missing/unknown values, strict timestamp acceptance and rejection,
chronological ordering and tie-breaks, transition validity and terminal states,
anomaly/summary accounting, permutation invariance, worked-example fixture
matching, and CLI error handling.

## Complexity and performance

Processing is dominated by per-order sorting: worst-case `O(N log N)` time and
`O(N)` memory in the number of input rows, using a single normalization pass plus
one sort per order. On one local Windows run with Python 3.12.6, the full supplied
dataset (181,775 records / 30,840 orders) completed in about 3.0 seconds. That
figure is a single observed measurement, not a benchmark or a guarantee; runtime
varies with hardware, storage, and input composition.

## Limitations

- **Strict timestamp acceptance.** Only the three full-second UTC forms are
  accepted; fractional seconds, offsets other than `+00:00`, non-padded fields,
  and impossible dates/times are rejected rather than coerced.
- **Whole-input memory use.** Rows are held in memory; the approach is sized for
  the supplied ~181k-row dataset, not unbounded streams.
- **`row_id` integrity is fatal.** Empty or duplicated `row_id` values abort the
  run as malformed input instead of producing an anomaly, preserving one anomaly
  per `row_id` and unambiguous accounting.
- Anomaly `details` text is deterministic but human-readable; only the stable
  fields should be treated as machine contracts.

## Tooling disclosure

OpenCode Desktop with the GLM-5.3-Flash model was used as an AI coding assistant
during development (implementation support, test drafting, and documentation
review). The runtime uses only Python's standard library and does not call,
embed, or otherwise require OpenCode, GLM, any other AI model, or any external
service.
