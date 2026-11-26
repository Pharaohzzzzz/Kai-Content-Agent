"""P8 command line for recording actual publications and reviewing results."""

import argparse
import csv
import json
import sys
from pathlib import Path

from review_data import FORMATS, PLATFORMS, Operations, confirm, import_metrics, register
from review_report import report


def production_fields(command):
    command.add_argument("--manual-minutes", help="Human production minutes allocated to this publication")
    command.add_argument("--production-cost-cny", help="Production cost allocated to this publication (CNY)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true", help="Use the isolated simulated workspace")
    commands = parser.add_subparsers(dest="command", required=True)

    pending = commands.add_parser("register", help="Associate a local P7 package with pending records")
    pending.add_argument("manifest", type=Path)
    pending.add_argument("--category", required=True)
    pending.add_argument("--platform", choices=PLATFORMS)
    pending.add_argument("--format", choices=FORMATS)
    production_fields(pending)

    actual = commands.add_parser("confirm", help="Record actual publication after the user confirms it")
    actual.add_argument("publication_id")
    actual.add_argument("--confirmed-published", action="store_true")
    actual.add_argument("--title", required=True)
    actual.add_argument("--published-at", required=True)
    actual.add_argument("--work-id")
    actual.add_argument("--url")
    production_fields(actual)

    commands.add_parser("list", help="List the pending and published records")
    metrics = commands.add_parser("import", help="Import cumulative metrics from CSV or JSONL")
    metrics.add_argument("file", type=Path)

    brief = commands.add_parser("report", help="Create a Markdown and JSON operation review")
    brief.add_argument("--start", help="First publication date, inclusive (Asia/Shanghai)")
    brief.add_argument("--through", help="Last publication date, inclusive (Asia/Shanghai)")
    brief.add_argument("--as-of", help="Use snapshots through this ISO 8601 time (default: now)")
    brief.add_argument("--name", help="Report folder name")
    brief.add_argument("--editorial", type=Path, help="Codex notes for this report's current numerical basis")

    args = parser.parse_args()
    operations = Operations(args.simulate)
    try:
        with operations.lock:
            if args.command == "report":
                print(report(args, operations))
                return 0
            if args.command == "list":
                _, _, records = operations.publications()
                result = [record for record, _ in records.values()]
            else:
                handler = {"register": register, "confirm": confirm, "import": import_metrics}[args.command]
                result = handler(args, operations)
            print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, AttributeError, TypeError, csv.Error) as error:
        print(f"Review error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
