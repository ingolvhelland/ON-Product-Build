"""
Pipeline status CLI (PB-038) - prints where every opportunity actually
stands, and what the next action on it is, using the registry in
`onbuild.pipeline`. Deliberately separate from `onbuild.overview`, which
keeps its one job: the admitted-ranked list a human chooses from. This
tool covers the whole pipeline, from opportunities with no evaluation
yet through to terminal outcomes - a status view, not a ranking.

Read-only - it does not call `close_expired_opportunities()` the way
`overview` does, since that's a state-changing side effect that belongs
to the tool whose job is the admitted list, not to a status report.

Run it directly:

    python -m onbuild.pipeline_status
"""

from onbuild.pipeline import classify_all


def main() -> None:
    classified = classify_all()
    if not classified:
        print("No opportunities in the system yet.")
        return

    print(f"{'#':<4} {'Title':<38} {'Org':<18} {'Stage':<28} {'Next action'}")
    print("-" * 160)
    for stage, snapshot in classified:
        print(
            f"{snapshot.opportunity_id:<4} {snapshot.title[:36]:<38} "
            f"{(snapshot.organisation or '')[:16]:<18} {stage.key:<28} "
            f"{stage.next_action(snapshot)}"
        )


if __name__ == "__main__":
    main()
