"""
Live mailbox scan - the automated front end to the outcome agent (PB-030),
built after Ingolv corrected PB-029's deferral: "no i want the system to
have live access, thats why i made a gmail account only for the job
search" and "i want the agent to monitor all incoming mail." Reads the
dedicated job-search Gmail account created for exactly this purpose - a
low-blast-radius account, not Ingolv's primary inbox, which is why live
access is appropriate here in a way it explicitly was not for
application-portal browser automation (PB-025).

Safety guarantees, by construction, not by convention:
- The mailbox is opened strictly read-only (`select(..., readonly=True)`) -
  IMAP itself refuses any STORE/EXPUNGE/DELETE command in this mode.
- Every fetch uses BODY.PEEK[], which never sets the Seen flag - nothing
  about the live account changes as a result of this script running.
- This module contains no SMTP/send capability anywhere, and never will -
  Action still never bypasses human approval (PB-002), monotonic; reading
  mail automatically does not loosen that, it only removes the manual
  copy-paste step PB-029 originally required.
- Idempotency is tracked entirely in the local database (`mailbox_state`),
  never by mutating anything on the live account (PB-007: the database is
  the sole source of truth).
- Matching a message to an opportunity is a plain heuristic (organisation
  name against sender/subject/body) - a genuine mismatch only risks the
  outcome agent classifying the wrong opportunity's message, which
  `onbuild.outcome_decision --override <opportunity_id>` exists to correct
  (PB-040). This script itself never touches `lifecycle_status` or
  anything else - it only decides which opportunity a message belongs to
  and hands it to `onbuild.agents.outcome`, which is where classification
  and (since PB-040) direct application actually happen.
- Zero or multiple candidate matches are never guessed at - the message
  first goes through `onbuild.agents.scan`'s mailbox-extraction entry
  point (PB-042: many companies ask permission to send future
  opportunities on application, so this inbox will accumulate exactly
  those), and only if that finds nothing is it held in
  `unmatched_mailbox_messages` for manual resolution - never silently
  dropped either way (PB-004's discovery-capture discipline, applied to
  live mail).

Credentials: GMAIL_ADDRESS / GMAIL_APP_PASSWORD in .env - an App Password
(Google Account > Security > 2-Step Verification > App passwords), scoped
to this one dedicated account and independently revocable, not the
account's real login password.

Run mode is a single pass over everything new since the last run - safe to
run repeatedly, by hand or on a schedule; this module does not itself set
up any recurring schedule (a standing/persistent configuration change is
Ingolv's own call, not made here).

    python -m onbuild.mailbox
"""

import asyncio
import email
import imaplib
import json
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

from onbuild.agents import outcome, scan
from onbuild.db import evidence_ops
from onbuild.db.schema import init_db

load_dotenv()

IMAP_HOST = "imap.gmail.com"


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def _extract_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        plain, html = None, None
        for part in msg.walk():
            content_type = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if content_type == "text/plain" and plain is None:
                plain = text
            elif content_type == "text/html" and html is None:
                html = text
        if plain is not None:
            return plain
        if html is not None:
            return html
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return msg.get_payload() or ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def _connect() -> imaplib.IMAP4_SSL:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env - see "
            ".env.example for how to generate an App Password."
        )
    imap = imaplib.IMAP4_SSL(IMAP_HOST)
    imap.login(address, app_password)
    # readonly=True: IMAP refuses any STORE/EXPUNGE/DELETE in this mode -
    # the mailbox cannot be modified by this connection even by accident.
    imap.select("INBOX", readonly=True)
    return imap


def _fetch_new_messages(imap: imaplib.IMAP4_SSL, last_uid: int) -> list[tuple[int, email.message.Message]]:
    start = last_uid + 1
    status, data = imap.uid("search", None, f"UID {start}:*")
    if status != "OK" or not data or not data[0]:
        return []
    uids = [int(u) for u in data[0].split()]
    # A mailbox with nothing newer than `last_uid` can still return
    # `last_uid` itself from a "X:*" range search (IMAP's documented
    # fallback behaviour when the range's start exceeds every UID) -
    # filter it back out explicitly rather than trust the range alone.
    uids = [u for u in uids if u > last_uid]
    messages = []
    for uid in uids:
        # BODY.PEEK[] - fetches the full message without ever setting Seen.
        status, msg_data = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not msg_data or msg_data[0] is None:
            continue
        raw = msg_data[0][1]
        messages.append((uid, email.message_from_bytes(raw)))
    return messages


def _match_opportunity(sender: str, subject: str, body: str, candidates: list[tuple]) -> list[int]:
    haystack = f"{sender}\n{subject}\n{body}".lower()
    matches = []
    for opp_id, title, organisation in candidates:
        if organisation and organisation.strip().lower() in haystack:
            matches.append(opp_id)
    return matches


async def _process_message(uid: int, msg: email.message.Message) -> None:
    sender = _decode(msg.get("From"))
    subject = _decode(msg.get("Subject"))
    body = _extract_text_body(msg)
    date_header = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(date_header).isoformat() if date_header else None
    except (TypeError, ValueError):
        received_at = None

    message_text = f"From: {sender}\nSubject: {subject}\nDate: {date_header or '(unknown)'}\n\n{body}"

    candidates = evidence_ops.fetch_opportunities_awaiting_outcome()
    matched_ids = _match_opportunity(sender, subject, body, candidates)

    if len(matched_ids) == 1:
        opportunity_id = matched_ids[0]
        print(f"\n=== UID {uid}: matched opportunity #{opportunity_id} ({subject!r}) ===")
        await outcome.classify_message(opportunity_id, message_text)
    else:
        reason = "no candidate matched" if not matched_ids else f"{len(matched_ids)} candidates matched"
        print(f"\n=== UID {uid}: unmatched ({reason}) - {subject!r} from {sender!r} ===")
        # PB-042: not every unmatched message is outcome-relevant at all -
        # a company Ingolv already applied to often asks permission to
        # send future opportunities, and those land here too, since
        # they're not about any pending application. Try discovery first;
        # only fall back to holding it for manual outcome-review if
        # nothing was actually found (a genuinely ambiguous or unrelated
        # message still needs that path, unchanged).
        recorded_ids = await scan.extract_from_message(message_text, sender)
        if recorded_ids:
            print(f"    -> discovered {len(recorded_ids)} new candidate opportunity(ies): "
                  f"{', '.join(f'#{i}' for i in recorded_ids)}")
        else:
            evidence_ops.insert_unmatched_message(
                uid, sender, subject, message_text, json.dumps(matched_ids), received_at
            )

    evidence_ops.update_mailbox_last_uid(uid)


async def scan() -> None:
    init_db()
    last_uid = evidence_ops.fetch_mailbox_last_uid()
    imap = _connect()
    try:
        messages = _fetch_new_messages(imap, last_uid)
    finally:
        imap.logout()

    if not messages:
        print(f"No new messages since UID {last_uid}.")
        return

    print(f"{len(messages)} new message(s) since UID {last_uid}.")
    for uid, msg in messages:
        await _process_message(uid, msg)

    print(f"\nDone. Watermark advanced to UID {messages[-1][0]}.")


def main() -> None:
    asyncio.run(scan())


if __name__ == "__main__":
    main()
