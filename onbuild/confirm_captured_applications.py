"""
Confirm-captured-applications CLI (PB-050) - the human gate over
auto-captured external applications.

`onbuild.mailbox`/`onbuild.agents.outcome` can now recognize a receipt
or confirmation message for an application Ingolv sent entirely outside
this system (a quick LinkedIn Easy Apply, a direct email) and
auto-create the opportunity so it isn't lost - but that recognition
isn't infallible, so every auto-capture sits here, flagged and
unconfirmed, until Ingolv actually looks at it. Nothing about an
auto-captured opportunity is treated as fully trusted before that:
`onbuild.pipeline` surfaces it as its own distinct stage rather than
folding it into an ordinary `submitted_pending_outcome` opportunity.

'confirm' means yes, this really is an application Ingolv sent - clears
the flag; from that point it's indistinguishable from any other
`submitted_pending_outcome` opportunity, including one registered by
hand via `onbuild.register_external_application`. 'reject' means this
was a misread (not really about an application he sent) - closes it
with a clear note rather than leaving it live and untrustworthy.

Run it directly:

    python -m onbuild.confirm_captured_applications

Skipped items remain unconfirmed; re-run the tool anytime to continue.
"""

from onbuild.db import evidence_ops


def _prompt() -> str:
    while True:
        choice = input("[c]onfirm / [r]eject / [s]kip / [q]uit > ").strip().lower()
        if choice in ("c", "r", "s", "q"):
            return choice
        print("Please enter c, r, s, or q.")


def main() -> None:
    rows = evidence_ops.fetch_captured_applications_pending_confirmation()
    if not rows:
        print("No auto-captured applications awaiting confirmation.")
        return

    confirmed = rejected = skipped = 0
    for opp_id, title, organisation, auto_captured_at, receipt_message in rows:
        print(f"\n=== Opportunity #{opp_id}: {title} — {organisation or '(no organisation)'} ===")
        print(f"Auto-captured: {auto_captured_at}")
        print(f"\n{receipt_message}")

        choice = _prompt()
        if choice == "q":
            break
        if choice == "c":
            evidence_ops.confirm_captured_application(opp_id)
            confirmed += 1
        elif choice == "r":
            note = input("Note (optional, why this is wrong): ").strip()
            evidence_ops.reject_captured_application(opp_id, note or None)
            rejected += 1
        else:
            skipped += 1

    print(f"\n=== Summary ===\nConfirmed: {confirmed}\nRejected: {rejected}\nSkipped: {skipped}")
    print("\nSkipped items remain unconfirmed - re-run this tool anytime to continue.")


if __name__ == "__main__":
    main()
