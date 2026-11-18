"""Unattended cycle: discover hiring companies, match a contact, send the proposal."""

from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from talentcrafters.contacts import find_public_contacts, hunter_contacts, unsuitable_address
from talentcrafters.db import FROM_EMAIL, db, get_settings, log, now_iso, set_settings
from talentcrafters.discover import clean_site, collect, name_key
from talentcrafters.mailer import TRACK_EMAIL, MailboxError, deliver, notify_tracker, poll_replies
from talentcrafters.proposal import compose, follow_up

SAST = timezone(timedelta(hours=2))
_DISCOVERY_EVERY = 20 * 60
_LOOP_EVERY = 180
_SENDS_PER_CYCLE = 15
_CONTACT_BATCH = 6
_SEND_HOUR = 16
_FOLLOW_DAYS = 4
_FOLLOW_LIMIT = 3


def in_send_window(moment: datetime | None = None) -> bool:
    """Queued letters go out from 16:00 South African time."""
    if moment is None:
        moment = datetime.now(SAST)
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=SAST)
    else:
        moment = moment.astimezone(SAST)
    return moment.hour >= _SEND_HOUR


def window_label(moment: datetime | None = None) -> str:
    return "The search keeps going. Queued letters go out at 16:00 SAST."


def _suppressed(conn, email: str) -> bool:
    row = conn.execute("SELECT 1 FROM suppressions WHERE email=?", (email.lower(),)).fetchone()
    return row is not None


def suppress(conn, email: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO suppressions (email, reason, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(email) DO UPDATE SET reason=excluded.reason",
        (email.lower(), reason, now_iso()),
    )


def _store_jobs(conn, jobs: list[dict]) -> int:
    added = 0
    for job in jobs:
        key = name_key(job["company"])
        if not key:
            continue
        site = clean_site(job.get("website") or "")
        row = conn.execute("SELECT id, website FROM companies WHERE name_key=?", (key,)).fetchone()
        if row:
            company_id = row["id"]
            if site and not clean_site(row["website"] or ""):
                conn.execute(
                    "UPDATE companies SET website=? WHERE id=?",
                    (site, company_id),
                )
        else:
            cursor = conn.execute(
                "INSERT INTO companies (name, name_key, website, market, created_at, updated_at) "
                "VALUES (?, ?, ?, 'international', ?, ?)",
                (job["company"], key, site, now_iso(), now_iso()),
            )
            company_id = cursor.lastrowid
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO roles "
            "(company_id, title, location, url, source, market, description, discovered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                company_id,
                job["title"],
                job["location"],
                job["url"],
                job["source"],
                job["market"],
                job["description"],
                now_iso(),
            ),
        )
        if conn.total_changes > before:
            added += 1
            conn.execute(
                "UPDATE companies SET updated_at=? WHERE id=?",
                (now_iso(), company_id),
            )
            _touch_market(conn, company_id)
    return added


def _touch_market(conn, company_id: int) -> None:
    rows = conn.execute(
        "SELECT DISTINCT market FROM roles WHERE company_id=?", (company_id,)
    ).fetchall()
    markets = {row["market"] for row in rows}
    if "local" in markets and "international" in markets:
        market = "both"
    elif "local" in markets:
        market = "local"
    else:
        market = "international"
    conn.execute(
        "UPDATE companies SET market=? WHERE id=?",
        (market, company_id),
    )


def _company_roles(conn, company_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM roles WHERE company_id=? ORDER BY discovered_at DESC",
        (company_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _save_contact(conn, company_id: int, contact: dict) -> None:
    conn.execute(
        "INSERT INTO contacts (company_id, name, title, email, kind, source, auto_send, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(company_id, email) DO UPDATE SET "
        "name=excluded.name, title=excluded.title, kind=excluded.kind, "
        "source=excluded.source, auto_send=excluded.auto_send",
        (
            company_id,
            contact.get("name") or "",
            contact.get("title") or "",
            contact["email"].lower(),
            contact["kind"],
            contact["source"],
            int(contact["auto_send"]),
            now_iso(),
        ),
    )


def _unsub_url(settings: dict[str, str], token: str) -> str:
    base = (settings.get("public_base_url") or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return ""
    return f"{base}/unsubscribe/{token}"


def _draft_for(conn, settings: dict[str, str], contact_row, company_row, roles: list[dict]) -> None:
    if _suppressed(conn, contact_row["email"]):
        return
    existing = conn.execute(
        "SELECT id, status, unsub_token, retry_count, edited FROM outreach WHERE company_id=? AND to_email=?",
        (company_row["id"], contact_row["email"]),
    ).fetchone()
    if existing and int(existing["edited"] or 0):
        return
    if existing and existing["status"] in {"sent", "replied", "unsubscribed"}:
        return
    if existing and existing["status"] == "failed" and int(existing["retry_count"]) >= 2:
        return
    company_busy = conn.execute(
        "SELECT 1 FROM outreach WHERE company_id=? AND status IN ('queued', 'sent', 'replied', 'unsubscribed')",
        (company_row["id"],),
    ).fetchone()
    if company_busy and not existing:
        return
    primary = roles[0]
    token = existing["unsub_token"] if existing else secrets.token_urlsafe(18)
    subject, body_text, body_html = compose(
        company=company_row["name"],
        title=primary["title"],
        location=primary["location"],
        market=primary["market"],
        extra_roles=max(0, len(roles) - 1),
        contact_name=contact_row["name"] or "",
        advantages=settings.get("advantages") or "",
        postal_address=settings.get("postal_address") or "",
        unsub_url=_unsub_url(settings, token),
    )
    if existing:
        conn.execute(
            "UPDATE outreach SET subject=?, body_text=?, body_html=?, role_id=?, contact_id=?, status='queued', error='' WHERE id=?",
            (subject, body_text, body_html, primary["id"], contact_row["id"], existing["id"]),
        )
        return
    conn.execute(
        "INSERT INTO outreach "
        "(company_id, contact_id, role_id, to_email, subject, body_text, body_html, status, unsub_token, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
        (
            company_row["id"],
            contact_row["id"],
            primary["id"],
            contact_row["email"],
            subject,
            body_text,
            body_html,
            token,
            now_iso(),
        ),
    )


def redraft_queued() -> int:
    with db() as conn:
        settings = get_settings(conn)
        rows = conn.execute(
            "SELECT o.id, o.company_id, o.contact_id FROM outreach o WHERE o.status='queued'"
        ).fetchall()
        count = 0
        for row in rows:
            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
            company = conn.execute("SELECT * FROM companies WHERE id=?", (row["company_id"],)).fetchone()
            if not contact or not company:
                continue
            _draft_for(conn, settings, contact, company, _company_roles(conn, company["id"]))
            count += 1
        return count


def _due_follow_ups(conn, limit: int = _SENDS_PER_CYCLE) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_FOLLOW_DAYS)).replace(microsecond=0).isoformat()
    rows = conn.execute(
        """
        SELECT o.*, c.name AS company_name
        FROM outreach o
        JOIN companies c ON c.id = o.company_id
        WHERE o.status='sent'
          AND COALESCE(o.follow_ups, 0) < ?
          AND (
            (COALESCE(o.follow_ups, 0)=0 AND o.sent_at IS NOT NULL AND o.sent_at<=?)
            OR (COALESCE(o.follow_ups, 0)>0 AND o.followed_at IS NOT NULL AND o.followed_at<=?)
          )
        ORDER BY o.sent_at
        LIMIT ?
        """,
        (_FOLLOW_LIMIT, cutoff, cutoff, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _sent_since(conn, start: datetime) -> int:
    start_utc = start.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    sent = conn.execute(
        "SELECT COUNT(*) AS n FROM outreach WHERE status='sent' AND sent_at>=?",
        (start_utc,),
    ).fetchone()
    followed = conn.execute(
        "SELECT COUNT(*) AS n FROM outreach WHERE followed_at>=?",
        (start_utc,),
    ).fetchone()
    return int(sent["n"]) + int(followed["n"])


def _sent_today(conn) -> int:
    start = datetime.now(SAST).replace(hour=0, minute=0, second=0, microsecond=0)
    return _sent_since(conn, start)


def _sent_week(conn) -> int:
    now = datetime.now(SAST)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return _sent_since(conn, start)


def _sent_month(conn) -> int:
    start = datetime.now(SAST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _sent_since(conn, start)


def _send_block_reason(settings: dict[str, str], sent_today: int) -> str:
    if settings.get("paused") == "1":
        return "The agent is paused."
    if settings.get("confirmed") != "1":
        return "Confirm sending on the Agent page. The search keeps running until then."
    if not (settings.get("smtp_password") or "").strip():
        return "Connect the hr@talentcrafters.co.za mailbox and proposals will start going out."
    if settings.get("smtp_error"):
        return settings["smtp_error"]
    if not (settings.get("postal_address") or "").strip():
        return "Add the company postal address. It is printed on every proposal."
    return ""


def _cap(settings: dict[str, str]) -> int:
    try:
        value = int(settings.get("daily_cap") or "12")
    except ValueError:
        value = 12
    return max(1, min(40, value))


class Controller:
    def __init__(self) -> None:
        self.cycle_in_progress = False
        self.last_cycle_at = ""
        self.last_summary = "The agent is starting."
        self.last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake = threading.Event()
        self._force = False
        self._last_discovery = 0.0
        self._last_block = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="talent-crafters-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_cycle(self, force_discovery: bool = True) -> bool:
        if self.cycle_in_progress:
            return False
        self._force = force_discovery
        self._wake.set()
        return True

    def _loop(self) -> None:
        self._wake.wait(2)
        self._wake.clear()
        while not self._stop.is_set():
            try:
                self.run_cycle(force_discovery=self._force or self._last_discovery == 0.0)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:300]
                with db() as conn:
                    log(conn, "error", f"Cycle stopped early: {self.last_error}")
            self._force = False
            self._wake.wait(_LOOP_EVERY)
            self._wake.clear()

    def run_cycle(self, force_discovery: bool = False) -> None:
        if self.cycle_in_progress:
            return
        self.cycle_in_progress = True
        try:
            self._run(force_discovery)
        finally:
            self.cycle_in_progress = False
            self.last_cycle_at = now_iso()

    def _run(self, force_discovery: bool) -> None:
        with db() as conn:
            settings = get_settings(conn)
            paused = settings.get("paused") == "1"
        if paused:
            self.last_summary = "The agent is paused. Hiring search and sending are both stopped."
            return

        new_roles = 0
        notes: list[str] = []
        should_discover = force_discovery or (time.time() - self._last_discovery) >= _DISCOVERY_EVERY
        if should_discover:
            with httpx.Client(timeout=25, follow_redirects=True, headers={"User-Agent": "TalentCraftersOutreach/1.0 (hr@talentcrafters.co.za)"}) as client:
                with db() as conn:
                    settings = get_settings(conn)
                jobs, notes, updates = collect(client, settings)
            with db() as conn:
                new_roles = _store_jobs(conn, jobs)
                if updates:
                    set_settings(conn, updates)
                if new_roles:
                    log(conn, "info", f"Found {new_roles} new open roles. " + "; ".join(notes))
            self._last_discovery = time.time()

        new_contacts = self._match_contacts()
        drafted = self._queue_ready()
        replies = self._read_inbox()
        sent, failed, block = self._send_ready()

        summary_bits = []
        if should_discover:
            summary_bits.append(f"{new_roles} new roles")
        if new_contacts:
            summary_bits.append(
                f"{new_contacts} contact{'' if new_contacts == 1 else 's'} matched"
            )
        if drafted:
            summary_bits.append(
                f"{drafted} proposal{'' if drafted == 1 else 's'} written"
            )
        if not in_send_window() and not block:
            summary_bits.append("letters stay queued until 16:00 SAST")
        if sent:
            summary_bits.append(f"{sent} sent from {FROM_EMAIL}")
        if replies:
            summary_bits.append(f"{replies} replies read")
        if failed:
            summary_bits.append(f"{failed} failed")
        if not summary_bits:
            summary_bits.append("no new changes")
        if block and not sent:
            summary_bits.append(block)
        self.last_summary = "; ".join(summary_bits)
        self.last_error = ""
        if sent or failed or (should_discover and new_roles) or new_contacts:
            with db() as conn:
                log(conn, "sent" if sent and not failed else "info", self.last_summary[:500])

    def _match_contacts(self) -> int:
        cutoff = (datetime.now(SAST).astimezone(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()
        with db() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM companies c
                WHERE EXISTS (SELECT 1 FROM roles r WHERE r.company_id=c.id)
                  AND (
                    c.contact_checked_at IS NULL
                    OR (
                      c.contact_checked_at < ?
                      AND NOT EXISTS (
                        SELECT 1 FROM contacts ct
                        WHERE ct.company_id=c.id AND ct.auto_send=1
                      )
                    )
                  )
                ORDER BY c.contact_checked_at IS NOT NULL, c.updated_at DESC
                LIMIT ?
                """,
                (cutoff, _CONTACT_BATCH),
            ).fetchall()
            work = []
            for row in rows:
                roles = _company_roles(conn, row["id"])
                work.append((dict(row), roles))
            settings = get_settings(conn)

        matched = 0
        with httpx.Client(timeout=12, follow_redirects=True) as client:
            for company, roles in work:
                site, contacts = find_public_contacts(client, company.get("website") or "", roles)
                if not any(item["auto_send"] for item in contacts):
                    domain = ""
                    if site:
                        domain = urlparse(site).hostname or ""
                        if domain.startswith("www."):
                            domain = domain[4:]
                    if domain and settings.get("hunter_api_key"):
                        contacts.extend(
                            hunter_contacts(
                                client,
                                domain,
                                settings["hunter_api_key"],
                                settings.get("hunter_auto_send") == "1",
                            )
                        )
                rank = {"named": 2, "recruiting_inbox": 1, "general": 0}
                contacts.sort(
                    key=lambda item: (int(item["auto_send"]), rank.get(item["kind"], 0)),
                    reverse=True,
                )
                with db() as conn:
                    if site and not company.get("website"):
                        conn.execute(
                            "UPDATE companies SET website=? WHERE id=?",
                            (site, company["id"]),
                        )
                    for contact in contacts[:1]:
                        _save_contact(conn, company["id"], contact)
                        matched += 1
                    conn.execute(
                        "UPDATE companies SET contact_checked_at=? WHERE id=?",
                        (now_iso(), company["id"]),
                    )
                time.sleep(0.3)
        return matched

    def _queue_ready(self) -> int:
        with db() as conn:
            settings = get_settings(conn)
            contacts = conn.execute(
                """
                SELECT ct.*, c.name AS company_name
                FROM contacts ct
                JOIN companies c ON c.id=ct.company_id
                WHERE ct.auto_send=1
                ORDER BY ct.id DESC
                LIMIT 40
                """
            ).fetchall()
            drafted = 0
            for contact in contacts:
                before = conn.execute(
                    "SELECT COUNT(*) AS n FROM outreach WHERE company_id=?",
                    (contact["company_id"],),
                ).fetchone()["n"]
                roles = _company_roles(conn, contact["company_id"])
                if not roles:
                    continue
                company = conn.execute(
                    "SELECT * FROM companies WHERE id=?", (contact["company_id"],)
                ).fetchone()
                _draft_for(conn, settings, contact, company, roles)
                after = conn.execute(
                    "SELECT COUNT(*) AS n FROM outreach WHERE company_id=?",
                    (contact["company_id"],),
                ).fetchone()["n"]
                drafted += max(0, after - before)
            return drafted

    def _read_inbox(self) -> int:
        with db() as conn:
            settings = get_settings(conn)
            known = {
                row["to_email"].lower()
                for row in conn.execute("SELECT to_email FROM outreach")
            }
        if not settings.get("smtp_password"):
            return 0
        events = poll_replies(settings, known)
        changed = 0
        with db() as conn:
            for email, event in events:
                if event == "unsubscribed":
                    suppress(conn, email, "unsubscribe")
                    cursor = conn.execute(
                        "UPDATE outreach SET status='unsubscribed', error='' "
                        "WHERE lower(to_email)=? AND status!='unsubscribed'",
                        (email,),
                    )
                    if cursor.rowcount:
                        changed += cursor.rowcount
                        log(conn, "info", f"Stopped emailing {email} after an unsubscribe.")
                elif event == "replied":
                    cursor = conn.execute(
                        "UPDATE outreach SET status='replied' WHERE lower(to_email)=? AND status='sent'",
                        (email,),
                    )
                    if cursor.rowcount:
                        changed += cursor.rowcount
                        log(conn, "info", f"Reply received from {email}. No further proposal will go there.")
        return changed

    def _send_follow_ups(self) -> int:
        with db() as conn:
            settings = get_settings(conn)
            due = _due_follow_ups(conn)
        sent = 0
        for item in due:
            with db() as conn:
                if _suppressed(conn, item["to_email"]) or unsuitable_address(item["to_email"]):
                    conn.execute(
                        "UPDATE outreach SET follow_ups=? WHERE id=?",
                        (_FOLLOW_LIMIT, item["id"]),
                    )
                    continue
                settings = get_settings(conn)
            attempt = int(item.get("follow_ups") or 0) + 1
            subject, body_text, body_html = follow_up(
                company=item.get("company_name") or "",
                subject=item.get("subject") or "",
                postal_address=settings.get("postal_address") or "",
                attempt=attempt,
            )
            try:
                deliver(
                    settings,
                    item["to_email"],
                    subject,
                    body_text,
                    body_html,
                    _unsub_url(settings, item["unsub_token"]),
                )
            except MailboxError as exc:
                with db() as conn:
                    log(conn, "error", f"Follow-up to {item['to_email']} failed: {exc}")
                if "rejected that login" in str(exc):
                    break
                continue
            with db() as conn:
                conn.execute(
                    "UPDATE outreach SET follow_ups=?, followed_at=?, error='' WHERE id=?",
                    (attempt, now_iso(), item["id"]),
                )
                log(conn, "sent", f"Follow-up {attempt} sent to {item['to_email']} from {FROM_EMAIL}.")
            self._note_tracker(
                settings,
                item.get("company_name") or "",
                {"to_email": item["to_email"], "subject": subject, "body_text": body_text},
            )
            sent += 1
            time.sleep(20)
        return sent

    def _send_ready(self) -> tuple[int, int, str]:
        with db() as conn:
            settings = get_settings(conn)
            sent_today = _sent_today(conn)
            reason = _send_block_reason(settings, sent_today)
            if reason:
                if reason != self._last_block:
                    log(conn, "warn", reason)
                    self._last_block = reason
                return 0, 0, reason
            self._last_block = ""
            if not in_send_window():
                return 0, 0, ""
            today = datetime.now(SAST).date().isoformat()
            if settings.get("last_send_day") == today and settings.get("batch_open") != "1":
                return 0, 0, ""
            if settings.get("last_send_day") != today:
                set_settings(conn, {"last_send_day": today, "batch_open": "1"})
            queued = conn.execute(
                "SELECT * FROM outreach WHERE status='queued' ORDER BY id ASC LIMIT ?",
                (_SENDS_PER_CYCLE,),
            ).fetchall()
            queue = [dict(row) for row in queued]

        sent = 0
        failed = 0
        for item in queue:
            with db() as conn:
                if _suppressed(conn, item["to_email"]):
                    conn.execute(
                        "UPDATE outreach SET status='unsubscribed', error='suppressed' WHERE id=?",
                        (item["id"],),
                    )
                    continue
                settings = get_settings(conn)
                contact = conn.execute(
                    "SELECT * FROM contacts WHERE id=?", (item["contact_id"],)
                ).fetchone()
                company = conn.execute(
                    "SELECT * FROM companies WHERE id=?", (item["company_id"],)
                ).fetchone()
                roles = _company_roles(conn, item["company_id"])
                if contact and company and roles:
                    _draft_for(conn, settings, contact, company, roles)
                fresh = conn.execute("SELECT * FROM outreach WHERE id=?", (item["id"],)).fetchone()
                token = fresh["unsub_token"]
                payload = dict(fresh)
                settings = get_settings(conn)
            company_name = company["name"] if company else ""
            if unsuitable_address(payload["to_email"]):
                with db() as conn:
                    conn.execute(
                        "UPDATE outreach SET status='failed', error=? WHERE id=?",
                        ("That address is not a company contact.", payload["id"]),
                    )
                failed += 1
                continue
            try:
                deliver(
                    settings,
                    payload["to_email"],
                    payload["subject"],
                    payload["body_text"],
                    payload["body_html"],
                    _unsub_url(settings, token),
                )
            except MailboxError as exc:
                message = str(exc)
                with db() as conn:
                    if "rejected that login" in message:
                        set_settings(conn, {"smtp_error": message})
                        log(conn, "error", message)
                    else:
                        conn.execute(
                            "UPDATE outreach SET status='failed', error=?, retry_count=retry_count+1 WHERE id=?",
                            (message, payload["id"]),
                        )
                        log(conn, "error", f"Proposal to {payload['to_email']} failed: {message}")
                    failed += 1
                if "rejected that login" in message:
                    break
                continue
            with db() as conn:
                conn.execute(
                    "UPDATE outreach SET status='sent', sent_at=?, error='' WHERE id=?",
                    (now_iso(), payload["id"]),
                )
                set_settings(conn, {"smtp_error": ""})
                log(conn, "sent", f"Proposal sent to {payload['to_email']} from {FROM_EMAIL}.")
            self._note_tracker(settings, company_name, payload)
            sent += 1
            time.sleep(20)

        followed = self._send_follow_ups()
        sent += followed
        with db() as conn:
            waiting = conn.execute("SELECT COUNT(*) AS n FROM outreach WHERE status='queued'").fetchone()["n"]
            due = _due_follow_ups(conn)
            if waiting == 0 and not due:
                set_settings(conn, {"batch_open": "0"})
        return sent, failed, ""

    def send_one(self, outreach_id: int) -> None:
        """Send one queued proposal because someone chose that letter."""
        with db() as conn:
            row = conn.execute("SELECT * FROM outreach WHERE id=?", (outreach_id,)).fetchone()
            if not row:
                raise MailboxError("That proposal was not found.")
            if row["status"] not in {"queued", "failed"}:
                raise MailboxError("That proposal has already been sent.")
            if _suppressed(conn, row["to_email"]) or unsuitable_address(row["to_email"]):
                raise MailboxError("That address cannot be used.")
            settings = get_settings(conn)
            if not (settings.get("smtp_password") or "").strip():
                raise MailboxError("Connect the hr@talentcrafters.co.za mailbox first.")
            if not (settings.get("postal_address") or "").strip():
                raise MailboxError("Add the company postal address first.")
            contact = conn.execute("SELECT * FROM contacts WHERE id=?", (row["contact_id"],)).fetchone()
            company = conn.execute("SELECT * FROM companies WHERE id=?", (row["company_id"],)).fetchone()
            roles = _company_roles(conn, row["company_id"])
            if contact and company and roles:
                _draft_for(conn, settings, contact, company, roles)
            fresh = conn.execute("SELECT * FROM outreach WHERE id=?", (outreach_id,)).fetchone()
            payload = dict(fresh)
            settings = get_settings(conn)
        deliver(
            settings,
            payload["to_email"],
            payload["subject"],
            payload["body_text"],
            payload["body_html"],
            _unsub_url(settings, payload["unsub_token"]),
        )
        with db() as conn:
            conn.execute(
                "UPDATE outreach SET status='sent', sent_at=?, error='' WHERE id=?",
                (now_iso(), outreach_id),
            )
            set_settings(conn, {"smtp_error": ""})
            log(conn, "sent", f"Proposal sent to {payload['to_email']} from {FROM_EMAIL}.")
        self._note_tracker(settings, company["name"] if company else "", payload)

    def _note_tracker(self, settings, company_name: str, payload: dict) -> None:
        try:
            notify_tracker(
                settings,
                company_name,
                payload["to_email"],
                payload["subject"],
                payload["body_text"],
            )
        except MailboxError as exc:
            with db() as conn:
                log(conn, "warn", f"Tracking note to {TRACK_EMAIL} failed: {exc}")
            return
        with db() as conn:
            log(conn, "info", f"Tracking note sent to {TRACK_EMAIL} for {company_name}.")


controller = Controller()
