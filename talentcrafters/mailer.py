"""Send from hr@talentcrafters.co.za and watch that inbox for replies."""

from __future__ import annotations

import html as html_lib
import imaplib
import re
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from email.utils import formataddr, parseaddr, parsedate_to_datetime
from pathlib import Path

from talentcrafters.db import FROM_EMAIL, FROM_NAME

WEB = Path(__file__).resolve().parent.parent / "web"
TRACK_EMAIL = "support@techlypc.co.za"
SAST = timezone(timedelta(hours=2))
LOGO = WEB / "logo.jpg"
LOGO_CID = "logo@talentcrafters"


class MailboxError(Exception):
    pass


def _message(to_email: str, subject: str, body_text: str, body_html: str, unsub_url: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    message["To"] = to_email
    message["Subject"] = subject
    message["Reply-To"] = FROM_EMAIL
    unsubscribe = f"<mailto:{FROM_EMAIL}?subject=unsubscribe>"
    if unsub_url:
        unsubscribe += f", <{unsub_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message["List-Unsubscribe"] = unsubscribe
    html_body = body_html.replace("/static/logo.jpg", f"cid:{LOGO_CID}")
    message.set_content(body_text)
    message.add_alternative(html_body, subtype="html")
    if LOGO.exists():
        message.get_payload()[-1].add_related(
            LOGO.read_bytes(),
            maintype="image",
            subtype="jpeg",
            cid=f"<{LOGO_CID}>",
        )
    return message


def deliver(
    settings: dict[str, str],
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    unsub_url: str = "",
) -> None:
    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or "587")
    user = settings.get("smtp_user") or FROM_EMAIL
    password = settings.get("smtp_password") or ""
    if not password:
        raise MailboxError("The hr@talentcrafters.co.za mailbox password is not saved yet.")
    email = _message(to_email, subject, body_text, body_html, unsub_url)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(email)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailboxError("The mailbox rejected that login.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        detail = str(exc)
        if "password" in detail.lower():
            detail = type(exc).__name__
        raise MailboxError(detail[:240] or "The message could not be sent.") from exc


def notify_tracker(
    settings: dict[str, str],
    company: str,
    to_email: str,
    subject: str,
    body_text: str,
) -> None:
    """Tell support@techlypc.co.za that a proposal has gone out."""
    if to_email.lower() == TRACK_EMAIL:
        return
    when = datetime.now(SAST).strftime("%d %b %Y, %H:%M SAST")
    text = (
        f"A proposal was sent from {FROM_EMAIL}.\n\n"
        f"Company: {company}\n"
        f"To: {to_email}\n"
        f"Subject: {subject}\n"
        f"Sent: {when}\n\n"
        "Letter:\n"
        f"{body_text}\n"
    )
    letter = html_lib.escape(body_text).replace("\n", "<br>")
    page = (
        "<p>A proposal was sent from "
        f"{html_lib.escape(FROM_EMAIL)}.</p>"
        f"<p><b>Company:</b> {html_lib.escape(company)}<br>"
        f"<b>To:</b> {html_lib.escape(to_email)}<br>"
        f"<b>Subject:</b> {html_lib.escape(subject)}<br>"
        f"<b>Sent:</b> {html_lib.escape(when)}</p>"
        f"<p>{letter}</p>"
    )
    deliver(settings, TRACK_EMAIL, f"Proposal sent to {company}", text, page)


def send_test(settings: dict[str, str]) -> None:
    text = (
        "This is a mailbox check from the Talent Crafters outreach agent.\n\n"
        "Proposals to hiring companies will come from this same address: "
        f"{FROM_EMAIL}.\n"
    )
    html = (
        "<p>This is a mailbox check from the Talent Crafters outreach agent.</p>"
        f"<p>Proposals to hiring companies will come from this same address: {FROM_EMAIL}.</p>"
    )
    deliver(settings, FROM_EMAIL, "Talent Crafters mailbox check", text, html)


def poll_replies(settings: dict[str, str], known_emails: set[str]) -> list[tuple[str, str]]:
    """Return (email, event) for replies and unsubscribes. event is 'replied' or 'unsubscribed'."""
    password = settings.get("smtp_password") or ""
    if not password:
        return []
    host = settings.get("imap_host") or "imap.gmail.com"
    port = int(settings.get("imap_port") or "993")
    user = settings.get("smtp_user") or FROM_EMAIL
    since = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
    events: list[tuple[str, str]] = []
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        status, data = imap.search(None, f"(SINCE {since})")
        if status != "OK" or not data or not data[0]:
            imap.logout()
            return []
        ids = data[0].split()[-40:]
        for number in ids:
            status, fetched = imap.fetch(number, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
            if status != "OK" or not fetched:
                continue
            raw = b""
            for part in fetched:
                if isinstance(part, tuple) and part[1]:
                    raw = part[1]
                    break
            if not raw:
                continue
            header = BytesParser(policy=default).parsebytes(raw)
            sender = parseaddr(header.get("From", ""))[1].lower()
            subject = (header.get("Subject") or "").lower()
            if not sender or sender == FROM_EMAIL:
                continue
            if "unsubscribe" in subject:
                events.append((sender, "unsubscribed"))
            elif sender in known_emails:
                events.append((sender, "replied"))
        imap.logout()
    except (imaplib.IMAP4.error, OSError):
        return events
    return events


_unseen_lock = threading.Lock()
_unseen = {"count": 0, "at": 0.0, "busy": False}
_UNSEEN_EVERY = 30.0


def _remember_unseen(count: int) -> None:
    with _unseen_lock:
        _unseen["count"] = max(0, int(count))
        _unseen["at"] = time.monotonic()
        _unseen["busy"] = False


def _drop_one_unseen() -> None:
    with _unseen_lock:
        if _unseen["count"] > 0:
            _unseen["count"] -= 1


def unseen_count(settings: dict[str, str]) -> int:
    """Unread messages in the hr@ inbox. The mailbox is checked in the background."""
    now = time.monotonic()
    with _unseen_lock:
        count = _unseen["count"]
        start = (
            now - _unseen["at"] >= _UNSEEN_EVERY
            and not _unseen["busy"]
            and bool(settings.get("smtp_password"))
        )
        if start:
            _unseen["busy"] = True
            snapshot = {
                "smtp_password": settings.get("smtp_password") or "",
                "imap_host": settings.get("imap_host") or "",
                "imap_port": settings.get("imap_port") or "",
                "smtp_user": settings.get("smtp_user") or "",
            }
    if start:
        threading.Thread(target=_refresh_unseen, args=(snapshot,), daemon=True).start()
    return count


def _refresh_unseen(settings: dict[str, str]) -> None:
    try:
        count = _count_unseen(settings)
    except MailboxError:
        with _unseen_lock:
            _unseen["at"] = time.monotonic()
            _unseen["busy"] = False
        return
    _remember_unseen(count)


def _count_unseen(settings: dict[str, str]) -> int:
    imap = _mailbox(settings)
    try:
        imap.select("INBOX", readonly=True)
        status, data = imap.uid("SEARCH", None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return 0
        return len(data[0].split())
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("The inbox could not be read.") from exc
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def _flags_unseen(fetched) -> bool:
    for part in fetched or []:
        blob = part[0] if isinstance(part, tuple) else part
        if isinstance(blob, (bytes, bytearray)) and b"FLAGS" in blob:
            return b"\\Seen" not in blob.split(b"BODY", 1)[0]
    return False


def _mailbox(settings: dict[str, str]):
    password = settings.get("smtp_password") or ""
    if not password:
        raise MailboxError("The hr@talentcrafters.co.za mailbox password is not saved yet.")
    host = settings.get("imap_host") or "imap.gmail.com"
    port = int(settings.get("imap_port") or "993")
    user = settings.get("smtp_user") or FROM_EMAIL
    try:
        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("The mailbox could not be opened.") from exc
    return imap


def _header_bytes(fetched) -> bytes:
    for part in fetched or []:
        if isinstance(part, tuple) and part[1]:
            return part[1]
    return b""


def _when(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError, IndexError, OverflowError):
        return value


def _message_text(message) -> str:
    plain = message.get_body(preferencelist=("plain",))
    if plain is not None:
        text = plain.get_content()
        return text if isinstance(text, str) else str(text)
    html_part = message.get_body(preferencelist=("html",))
    if html_part is None:
        return ""
    raw = html_part.get_content()
    if not isinstance(raw, str):
        raw = str(raw)
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def list_inbox(settings: dict[str, str], limit: int = 80) -> list[dict]:
    """Newest messages in the hr@ inbox, without marking them read."""
    imap = _mailbox(settings)
    try:
        imap.select("INBOX", readonly=True)
        unseen_status, unseen_data = imap.uid("SEARCH", None, "UNSEEN")
        unseen_ids: set[str] = set()
        if unseen_status == "OK" and unseen_data and unseen_data[0]:
            unseen_ids = {item.decode() for item in unseen_data[0].split()}
        _remember_unseen(len(unseen_ids))
        status, data = imap.uid("SEARCH", None, "ALL")
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()[-limit:]
        messages = []
        for uid in reversed(uids):
            status, fetched = imap.uid(
                "FETCH",
                uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
            )
            raw = _header_bytes(fetched) if status == "OK" else b""
            if not raw:
                continue
            header = BytesParser(policy=default).parsebytes(raw)
            name, email = parseaddr(header.get("From", ""))
            messages.append({
                "uid": uid.decode(),
                "from_name": name,
                "from_email": email,
                "subject": str(header.get("Subject") or "(no subject)"),
                "date": _when(str(header.get("Date") or "")),
                "message_id": str(header.get("Message-ID") or ""),
                "unseen": uid.decode() in unseen_ids,
            })
        return messages
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("The inbox could not be read.") from exc
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def read_inbox(settings: dict[str, str], uid: str) -> dict:
    if not str(uid).isdigit():
        raise MailboxError("That message was not found.")
    imap = _mailbox(settings)
    try:
        imap.select("INBOX")
        status, fetched = imap.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
        raw = _header_bytes(fetched) if status == "OK" else b""
        if not raw:
            raise MailboxError("That message was not found.")
        if _flags_unseen(fetched):
            try:
                imap.uid("STORE", uid, "+FLAGS", "(\\Seen)")
                _drop_one_unseen()
            except (imaplib.IMAP4.error, OSError):
                pass
        message = BytesParser(policy=default).parsebytes(raw)
        name, email = parseaddr(message.get("From", ""))
        text = _message_text(message).strip()
        return {
            "uid": str(uid),
            "from_name": name,
            "from_email": email,
            "subject": str(message.get("Subject") or "(no subject)"),
            "date": _when(str(message.get("Date") or "")),
            "message_id": str(message.get("Message-ID") or ""),
            "body": text[:20000],
        }
    except MailboxError:
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailboxError("That message could not be opened.") from exc
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def send_inbox_reply(settings: dict[str, str], uid: str, body: str) -> dict:
    """Reply from hr@ to the person who wrote the selected inbox message."""
    original = read_inbox(settings, uid)
    recipient = (original.get("from_email") or "").strip()
    if not recipient or "@" not in recipient:
        raise MailboxError("That message has no address to answer.")
    if recipient.lower() == FROM_EMAIL.lower():
        raise MailboxError("That message was sent from this mailbox.")
    subject = original.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message = EmailMessage()
    message["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    message["To"] = recipient
    message["Subject"] = subject[:180]
    message["Reply-To"] = FROM_EMAIL
    if original.get("message_id"):
        message["In-Reply-To"] = original["message_id"]
        message["References"] = original["message_id"]
    message.set_content(body)
    host = settings.get("smtp_host") or "smtp.gmail.com"
    port = int(settings.get("smtp_port") or "587")
    user = settings.get("smtp_user") or FROM_EMAIL
    password = settings.get("smtp_password") or ""
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, password)
                smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailboxError("The mailbox rejected that login.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        detail = str(exc)
        if "password" in detail.lower():
            detail = type(exc).__name__
        raise MailboxError(detail[:240] or "The reply could not be sent.") from exc
    return {"to": recipient, "subject": subject}
