"""
Selection CLI (PB-039) - the gate between admission and the brief.

Admission (`onbuild.admission`) only says an opportunity is not a bad
fit; it does not say "pursue this now." The ranked list of admitted
opportunities (`onbuild.overview`) is where Ingolv actually decides
what to invest effort in - this tool records that specific choice,
setting `opportunities.selected_at`. It's a fact-only gate, the same
reasoning as `onbuild.submission_confirmation` and
`onbuild.hold_opportunity`: not a content judgment, just "I am choosing
to actively pursue this one, starting now." `onbuild.agents.brief`
requires both admission and selection before it will run (PB-039).

Presents admitted opportunities not yet selected, ranked the same way
as `onbuild.overview` (deadline soonest first, then fit_score). Nothing
here is irreversible in effect - selecting an opportunity only unlocks
brief-writing, it doesn't commit to anything past that; the real
judgment calls (continue/revise/drop) still happen at
`onbuild.brief_decision`.

Run it directly:

    python -m onbuild.selection

Skipped items stay unselected; re-run the tool anytime to continue.
"""

from onbuild.db import evidence_ops


def _prompt() -> str:
    while True:
        choice = input("[s]elect / [k]eep for later / [q]uit > ").strip().lower()
        if choice in ("s", "k", "q"):
            return choice
        print("Please enter s, k, or q.")


def main() -> None:
    rows = evidence_ops.fetch_admitted_unselected()
    if not rows:
        print("No admitted opportunities awaiting selection.")
        return

    selected = skipped = 0
    for (
        opp_id,
        title,
        organisation,
        track,
        deadline,
        lifecycle_note,
        fit_score,
        fit_tier,
        fit_summary,
    ) in rows:
        print(
            f"\n=== Opportunity #{opp_id}: {title} — "
            f"{organisation or '(no organisation)'} [{track}] ==="
        )
        print(f"Deadline: {deadline or '(none captured)'} | fit_score={fit_score} | fit_tier={fit_tier}")
        if lifecycle_note:
            print(f"Note: {lifecycle_note}")
        print(f"\nFit: {fit_summary}")

        choice = _prompt()
        if choice == "q":
            break
        if choice == "s":
            evidence_ops.mark_opportunity_selected(opp_id)
            selected += 1
        else:
            skipped += 1

    print(f"\n=== Summary ===\nSelected: {selected}\nSkipped: {skipped}")
    print("\nSkipped items remain unselected - re-run this tool anytime to continue.")


if __name__ == "__main__":
    main()
