"""Command-line entry point for the event timeline reconstruction engine.

Usage:
    python main.py --input PATH --output-dir DIR
"""

from __future__ import annotations

import argparse
import sys

from engine import EngineError, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Reconstruct e-commerce order event timelines from a CSV and emit a "
            "per-order summary plus an anomaly report."
        ),
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to the input order-events CSV.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write order_summary.csv and anomalies.csv (created if needed).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args.input, args.output_dir)
    except EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
