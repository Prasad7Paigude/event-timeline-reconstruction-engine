"""Core engine for the Catalysk event timeline reconstruction assessment.

The module is import-safe: importing it performs no I/O and never runs the CLI.
All logic lives in small, pure-ish functions that take explicit arguments so the
pipeline can be exercised directly by tests without spawning a subprocess.

Pipeline overview:
    load_rows -> resolve_event_ids -> validate_row -> reconstruct_order
              -> build summaries -> write_outputs
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Sequence

REQUIRED_COLUMNS: tuple[str, ...] = (
    "row_id",
    "event_id",
    "order_id",
    "event_type",
    "event_time",
    "received_at",
    "source_system",
    "metadata",
)

EVENT_VOCABULARY: frozenset[str] = frozenset(
    {
        "PLACED",
        "PAID",
        "PACKED",
        "SHIPPED",
        "OUT_FOR_DELIVERY",
        "DELIVERED",
        "CANCELLED",
        "RETURN_REQUESTED",
        "RETURNED",
        "REFUNDED",
    }
)

# Allowed transitions. CANCELLED and REFUNDED are terminal (empty sets).
TRANSITIONS: dict[str, frozenset[str]] = {
    "START": frozenset({"PLACED"}),
    "PLACED": frozenset({"PAID", "CANCELLED"}),
    "PAID": frozenset({"PACKED", "CANCELLED"}),
    "PACKED": frozenset({"SHIPPED", "CANCELLED"}),
    "SHIPPED": frozenset({"OUT_FOR_DELIVERY"}),
    "OUT_FOR_DELIVERY": frozenset({"DELIVERED"}),
    "DELIVERED": frozenset({"RETURN_REQUESTED"}),
    "RETURN_REQUESTED": frozenset({"RETURNED"}),
    "RETURNED": frozenset({"REFUNDED"}),
    "CANCELLED": frozenset(),
    "REFUNDED": frozenset(),
}

SUMMARY_HEADER: tuple[str, ...] = (
    "order_id",
    "final_status",
    "valid_events",
    "anomaly_count",
    "first_valid_event_time",
    "last_valid_event_time",
)

ANOMALY_HEADER: tuple[str, ...] = (
    "row_id",
    "order_id",
    "event_id",
    "anomaly_type",
    "details",
)

# The only three supported full-second UTC representations. Each is validated
# first by an exact full-string pattern and then by strptime for real calendar
# and wall-clock validity (rejects 2026-02-30, 24:00:00, leap seconds, ...).
_TIMESTAMP_FORMATS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"), "%Y-%m-%dT%H:%M:%SZ"),
    (re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+00:00"), "%Y-%m-%dT%H:%M:%S+00:00"),
)


class EngineError(Exception):
    """Raised for unreadable/malformed input, invariant violations, or output failures."""


class Row(NamedTuple):
    """A single normalized input record (informational columns intentionally dropped)."""

    row_id: str
    event_id: str
    order_id: str
    event_type: str  # normalized: trimmed then uppercased, nothing else
    event_time: str  # trimmed raw value, not yet parsed


class Anomaly(NamedTuple):
    row_id: str
    order_id: str
    event_id: str
    anomaly_type: str
    details: str


class OrderSummary(NamedTuple):
    order_id: str
    final_status: str
    valid_events: int
    anomaly_count: int
    first_valid_event_time: str
    last_valid_event_time: str


def parse_timestamp(value: str) -> datetime | None:
    """Parse one of the three supported full-second UTC forms.

    Returns a timezone-aware UTC datetime, or ``None`` when the value is not in
    an exactly supported form. Never raises for malformed input.
    """
    text = value.strip()
    for pattern, fmt in _TIMESTAMP_FORMATS:
        if pattern.fullmatch(text):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                return None
            return parsed.replace(tzinfo=timezone.utc)
    return None


def format_timestamp(value: datetime) -> str:
    """Render a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cell(values: Sequence[str], index: int) -> str:
    """Return the trimmed value at ``index``, or empty when the cell is absent."""
    if 0 <= index < len(values):
        value = values[index]
        if value is not None:
            return value.strip()
    return ""


def load_rows(input_path: str | Path) -> list[Row]:
    """Read and normalize every input row.

    Trims all values, normalizes ``event_type`` (trim + uppercase only), and
    enforces the documented ``row_id`` integrity policy. Raises ``EngineError``
    for a missing/unreadable file, malformed CSV, missing required columns, or a
    row_id that is empty or duplicated.
    """
    path = Path(input_path)
    if not path.exists():
        raise EngineError(f"Input file not found: {input_path}")
    if not path.is_file():
        raise EngineError(f"Input path is not a file: {input_path}")

    rows: list[Row] = []
    seen_row_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            try:
                raw_header = next(reader)
            except StopIteration:
                raise EngineError("Input file is empty; a header row is required") from None

            header = [name.strip() for name in raw_header]
            index = {name: position for position, name in enumerate(header)}
            missing = [name for name in REQUIRED_COLUMNS if name not in index]
            if missing:
                raise EngineError("Missing required column(s): " + ", ".join(missing))

            idx_row = index["row_id"]
            idx_event = index["event_id"]
            idx_order = index["order_id"]
            idx_type = index["event_type"]
            idx_time = index["event_time"]

            for line_number, values in enumerate(reader, start=2):
                if not values:
                    continue  # stray blank line: not a data record
                row_id = _cell(values, idx_row)
                if row_id == "":
                    raise EngineError(
                        f"Input integrity violation: empty row_id at line {line_number}"
                    )
                if row_id in seen_row_ids:
                    raise EngineError(
                        f"Input integrity violation: duplicate row_id {row_id!r} "
                        f"at line {line_number}"
                    )
                seen_row_ids.add(row_id)
                rows.append(
                    Row(
                        row_id=row_id,
                        event_id=_cell(values, idx_event),
                        order_id=_cell(values, idx_order),
                        event_type=_cell(values, idx_type).upper(),
                        event_time=_cell(values, idx_time),
                    )
                )
    except UnicodeDecodeError as exc:
        raise EngineError(
            f"Input file {input_path!r} is not valid UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise EngineError(f"Cannot read input file {input_path!r}: {exc}") from exc
    except csv.Error as exc:
        raise EngineError(f"Malformed CSV input: {exc}") from exc
    return rows


def resolve_event_ids(rows: Sequence[Row]) -> tuple[list[Row], list[Anomaly]]:
    """Return the canonical rows plus duplicate/conflict anomalies.

    Only nonempty ``event_id`` values are grouped. The canonical row per group is
    the one with the lexicographically smallest trimmed ``row_id``. Every other
    row is rejected as ``DUPLICATE_EVENT`` (identity tuple equal to canonical) or
    ``EVENT_ID_CONFLICT`` (identity tuple differs). Rows with empty ``event_id``
    are never grouped and are passed straight through for validation.
    """
    canonical: dict[str, Row] = {}
    for row in rows:
        if row.event_id == "":
            continue
        current = canonical.get(row.event_id)
        if current is None or row.row_id < current.row_id:
            canonical[row.event_id] = row

    kept: list[Row] = []
    anomalies: list[Anomaly] = []
    for row in rows:
        if row.event_id == "":
            kept.append(row)
            continue
        reference = canonical[row.event_id]
        if row is reference:
            kept.append(row)
            continue
        if (row.order_id, row.event_type, row.event_time) == (
            reference.order_id,
            reference.event_type,
            reference.event_time,
        ):
            anomalies.append(
                Anomaly(
                    row.row_id,
                    row.order_id,
                    row.event_id,
                    "DUPLICATE_EVENT",
                    "Repeated event_id and matching event identity",
                )
            )
        else:
            anomalies.append(
                Anomaly(
                    row.row_id,
                    row.order_id,
                    row.event_id,
                    "EVENT_ID_CONFLICT",
                    "Repeated event_id with differing event identity",
                )
            )
    return kept, anomalies


def validate_row(row: Row) -> tuple[str | None, str, datetime | None]:
    """Apply the single-row validation precedence to one canonical row.

    Returns ``(anomaly_type, details, parsed_time)``. On success ``anomaly_type``
    is ``None`` and ``parsed_time`` is set; otherwise ``parsed_time`` is ``None``
    and exactly the first applicable anomaly is returned.
    """
    if row.order_id == "":
        return "MISSING_ORDER_ID", "Empty order_id", None
    if row.event_id == "":
        return "MISSING_EVENT_ID", "Empty event_id", None
    if row.event_time == "":
        return "MISSING_TIMESTAMP", "Empty event_time", None
    parsed = parse_timestamp(row.event_time)
    if parsed is None:
        return "INVALID_TIMESTAMP", "Cannot parse event_time as supported UTC format", None
    if row.event_type not in EVENT_VOCABULARY:
        return "UNKNOWN_EVENT", "Unknown/empty event_type", None
    return None, "", parsed


def reconstruct_order(
    order_id: str, events: Sequence[tuple[datetime, Row]]
) -> tuple[str, int, str, str, list[Anomaly]]:
    """Replay one order's eligible events through the state machine.

    ``events`` must already be sorted by ``(parsed event_time, event_id, row_id)``.
    Invalid transitions emit an anomaly, leave the state unchanged, and do not
    stop later processing. Returns
    ``(final_status, valid_events, first_time, last_time, anomalies)``.
    """
    state = "START"
    valid_events = 0
    first_time = ""
    last_time = ""
    anomalies: list[Anomaly] = []

    for parsed, row in events:
        if row.event_type in TRANSITIONS[state]:
            state = row.event_type
            valid_events += 1
            formatted = format_timestamp(parsed)
            if first_time == "":
                first_time = formatted
            last_time = formatted
        else:
            anomalies.append(
                Anomaly(
                    row.row_id,
                    order_id,
                    row.event_id,
                    "INVALID_TRANSITION",
                    f"{state} -> {row.event_type} is not allowed",
                )
            )

    final_status = state if valid_events > 0 else "UNKNOWN"
    return final_status, valid_events, first_time, last_time, anomalies


def build_summaries(
    order_ids: Sequence[str],
    results: dict[str, tuple[str, int, str, str, list[Anomaly]]],
    anomalies: Sequence[Anomaly],
) -> list[OrderSummary]:
    """Build one summary per order, with anomaly counts reconciled from all anomalies."""
    counts = Counter(a.order_id for a in anomalies if a.order_id != "")
    summaries: list[OrderSummary] = []
    for order_id in order_ids:
        final_status, valid_events, first_time, last_time, _ = results[order_id]
        summaries.append(
            OrderSummary(
                order_id=order_id,
                final_status=final_status,
                valid_events=valid_events,
                anomaly_count=counts.get(order_id, 0),
                first_valid_event_time=first_time,
                last_valid_event_time=last_time,
            )
        )
    return summaries


def write_outputs(
    output_dir: str | Path,
    summaries: Sequence[OrderSummary],
    anomalies: Sequence[Anomaly],
) -> None:
    """Write ``order_summary.csv`` and ``anomalies.csv`` into ``output_dir``.

    Both files are always written (headers only when there is no data). Rows are
    sorted by ``order_id`` and ``row_id`` respectively. Raises ``EngineError`` if
    the directory cannot be created or the files cannot be written.
    """
    directory = Path(output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EngineError(f"Cannot create output directory {output_dir!r}: {exc}") from exc

    ordered_summaries = sorted(summaries, key=lambda summary: summary.order_id)
    ordered_anomalies = sorted(anomalies, key=lambda anomaly: anomaly.row_id)

    try:
        with (directory / "order_summary.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(SUMMARY_HEADER)
            for summary in ordered_summaries:
                writer.writerow(
                    [
                        summary.order_id,
                        summary.final_status,
                        summary.valid_events,
                        summary.anomaly_count,
                        summary.first_valid_event_time,
                        summary.last_valid_event_time,
                    ]
                )

        with (directory / "anomalies.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(ANOMALY_HEADER)
            for anomaly in ordered_anomalies:
                writer.writerow(
                    [
                        anomaly.row_id,
                        anomaly.order_id,
                        anomaly.event_id,
                        anomaly.anomaly_type,
                        anomaly.details,
                    ]
                )
    except OSError as exc:
        raise EngineError(f"Cannot write output files: {exc}") from exc


def run(
    input_path: str | Path, output_dir: str | Path
) -> tuple[list[OrderSummary], list[Anomaly]]:
    """Run the full pipeline and write both output files.

    Returns the summaries and the complete anomaly list for inspection/testing.
    """
    rows = load_rows(input_path)

    # Every distinct nonempty order_id anywhere in the input, including orders
    # that only appear in rejected rows.
    order_ids = sorted({row.order_id for row in rows if row.order_id != ""})

    canonical_rows, anomalies = resolve_event_ids(rows)

    eligible_by_order: dict[str, list[tuple[datetime, Row]]] = {}
    for row in canonical_rows:
        anomaly_type, details, parsed = validate_row(row)
        if anomaly_type is None:
            assert parsed is not None
            eligible_by_order.setdefault(row.order_id, []).append((parsed, row))
        else:
            anomalies.append(
                Anomaly(row.row_id, row.order_id, row.event_id, anomaly_type, details)
            )

    results: dict[str, tuple[str, int, str, str, list[Anomaly]]] = {}
    for order_id in order_ids:
        events = sorted(
            eligible_by_order.get(order_id, []),
            key=lambda item: (item[0], item[1].event_id, item[1].row_id),
        )
        result = reconstruct_order(order_id, events)
        results[order_id] = result
        anomalies.extend(result[4])

    summaries = build_summaries(order_ids, results, anomalies)
    write_outputs(output_dir, summaries, anomalies)
    return summaries, anomalies
