"""
Scan-sources CLI (PB-042) - manages the job-board URLs
`onbuild.agents.scan` checks each run, without touching code. A plain
database table, not a config file, so it stays consistent with
everything else here (the database is the one source of truth, PB-007).

Disabling a source (rather than deleting it) is deliberate, same
append-only discipline as everything else in this system - the row
stays for history, `onbuild.agents.scan` just stops checking it.

Run it directly:

    python -m onbuild.scan_sources list
    python -m onbuild.scan_sources add "<label>" <url>
    python -m onbuild.scan_sources enable <id>
    python -m onbuild.scan_sources disable <id>
"""

import argparse

from onbuild.db import evidence_ops


def _list() -> None:
    rows = evidence_ops.list_scan_sources()
    if not rows:
        print("No scan sources configured yet. Add one with 'add \"<label>\" <url>'.")
        return
    for source_id, label, url, active in rows:
        status = "active" if active else "disabled"
        print(f"#{source_id:<3} [{status:<8}] {label} - {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the scanning agent's job-board sources.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List every configured source.")

    add_parser = sub.add_parser("add", help="Add a new source.")
    add_parser.add_argument("label")
    add_parser.add_argument("url")

    enable_parser = sub.add_parser("enable", help="Re-enable a disabled source.")
    enable_parser.add_argument("source_id", type=int)

    disable_parser = sub.add_parser("disable", help="Stop checking a source without deleting it.")
    disable_parser.add_argument("source_id", type=int)

    args = parser.parse_args()

    if args.command == "list":
        _list()
    elif args.command == "add":
        source_id = evidence_ops.insert_scan_source(args.label, args.url)
        print(f"Added source #{source_id}: {args.label} - {args.url}")
    elif args.command == "enable":
        evidence_ops.set_scan_source_active(args.source_id, True)
        print(f"Source #{args.source_id} enabled.")
    elif args.command == "disable":
        evidence_ops.set_scan_source_active(args.source_id, False)
        print(f"Source #{args.source_id} disabled.")


if __name__ == "__main__":
    main()
