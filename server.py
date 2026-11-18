"""Local dashboard for the Talent Crafters outreach agent."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from talentcrafters.agent import (
    _cap,
    _send_block_reason,
    _sent_month,
    _sent_today,
    _sent_week,
    controller,
    in_send_window,
    redraft_queued,
    suppress,
    window_label,
)
from talentcrafters.contacts import classify_email, extract_emails, unsuitable_address
from talentcrafters.db import (
    DEFAULT_ADVANTAGES,
    FROM_EMAIL,
    FROM_NAME,
    db,
    get_settings,
    init_db,
    log,
    now_iso,
    set_settings,
)
from talentcrafters.mailer import (
    MailboxError,
    list_inbox,
    read_inbox,
    send_inbox_reply,
    send_test,
    unseen_count,
)
from talentcrafters.proposal import render_plain

WEB = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if os.environ.get("VERCEL"):
        yield
        return
    with db() as conn:
        log(conn, "info", "Agent started. Searching for companies hiring in South Africa and internationally.")
    controller.start()
    print("Talent Crafters dashboard: http://127.0.0.1:8765", flush=True)
    yield
    controller.stop()


app = FastAPI(title="Talent Crafters", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/letters/{letter_id}")
def letter(letter_id: int):
    with db() as conn:
        row = conn.execute("SELECT body_html FROM outreach WHERE id=?", (letter_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Proposal not found.")
    return HTMLResponse(row["body_html"], headers={"Cache-Control": "no-store"})


def _public_settings(settings: dict[str, str]) -> dict:
    return {
        "smtp_host": settings["smtp_host"],
        "smtp_port": settings["smtp_port"],
        "smtp_user": settings["smtp_user"],
        "smtp_password_set": bool(settings.get("smtp_password")),
        "imap_host": settings["imap_host"],
        "imap_port": settings["imap_port"],
        "postal_address": settings["postal_address"],
        "daily_cap": settings["daily_cap"],
        "advantages": settings["advantages"],
        "confirmed": settings["confirmed"] == "1",
        "paused": settings["paused"] == "1",
        "hunter_api_key_set": bool(settings.get("hunter_api_key")),
        "hunter_auto_send": settings.get("hunter_auto_send") == "1",
        "adzuna_app_id_set": bool(settings.get("adzuna_app_id")),
        "adzuna_app_key_set": bool(settings.get("adzuna_app_key")),
        "public_base_url": settings.get("public_base_url") or "",
        "from_email": FROM_EMAIL,
        "from_name": FROM_NAME,
        "smtp_error": settings.get("smtp_error") or "",
    }


def _flag(value) -> str:
    return "1" if value in (True, 1, "1", "true", "True", "on") else "0"


def _apply_settings(payload: dict) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(400, "Settings must be an object.")
    updates: dict[str, str] = {}

    def optional_text(key: str, limit: int, fallback: str = "") -> None:
        if key not in payload:
            return
        value = str(payload.get(key) or "").strip()
        updates[key] = (value or fallback)[:limit]

    optional_text("smtp_host", 200, "smtp.gmail.com")
    optional_text("smtp_user", 200, FROM_EMAIL)
    optional_text("imap_host", 200, "imap.gmail.com")
    optional_text("postal_address", 500)
    optional_text("public_base_url", 300)
    if "public_base_url" in updates:
        url = updates["public_base_url"].rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(400, "The public base URL needs to start with http:// or https://.")
        updates["public_base_url"] = url
    if "smtp_port" in payload:
        try:
            port = int(payload["smtp_port"])
        except (TypeError, ValueError):
            raise HTTPException(400, "SMTP port needs to be a number.") from None
        if not 1 <= port <= 65535:
            raise HTTPException(400, "SMTP port is out of range.")
        updates["smtp_port"] = str(port)
    if "imap_port" in payload:
        try:
            port = int(payload["imap_port"])
        except (TypeError, ValueError):
            raise HTTPException(400, "IMAP port needs to be a number.") from None
        if not 1 <= port <= 65535:
            raise HTTPException(400, "IMAP port is out of range.")
        updates["imap_port"] = str(port)
    if "daily_cap" in payload:
        try:
            cap = int(payload["daily_cap"])
        except (TypeError, ValueError):
            raise HTTPException(400, "Daily cap needs to be a number.") from None
        updates["daily_cap"] = str(max(1, min(40, cap)))
    if "advantages" in payload:
        text = str(payload.get("advantages") or "").strip()
        updates["advantages"] = (text or DEFAULT_ADVANTAGES)[:2000]
    if payload.get("smtp_password"):
        updates["smtp_password"] = str(payload["smtp_password"])
        updates["smtp_error"] = ""
    if payload.get("hunter_api_key"):
        updates["hunter_api_key"] = str(payload["hunter_api_key"]).strip()[:200]
    if payload.get("adzuna_app_id"):
        updates["adzuna_app_id"] = str(payload["adzuna_app_id"]).strip()[:100]
    if payload.get("adzuna_app_key"):
        updates["adzuna_app_key"] = str(payload["adzuna_app_key"]).strip()[:100]
    if "confirmed" in payload:
        updates["confirmed"] = _flag(payload["confirmed"])
    if "paused" in payload:
        updates["paused"] = _flag(payload["paused"])
    if "hunter_auto_send" in payload:
        updates["hunter_auto_send"] = _flag(payload["hunter_auto_send"])
    return updates


def _company_payload(conn) -> list[dict]:
    companies = conn.execute(
        "SELECT * FROM companies ORDER BY updated_at DESC LIMIT 150"
    ).fetchall()
    if not companies:
        return []
    ids = [row["id"] for row in companies]
    marks = ",".join("?" for _ in ids)
    roles: dict[int, list] = {item: [] for item in ids}
    for row in conn.execute(
        f"SELECT * FROM roles WHERE company_id IN ({marks}) ORDER BY discovered_at DESC",
        ids,
    ):
        bucket = roles[row["company_id"]]
        if len(bucket) >= 4:
            continue
        bucket.append(
            {
                "id": row["id"],
                "title": row["title"],
                "location": row["location"],
                "url": row["url"],
                "source": row["source"],
                "market": row["market"],
            }
        )
    role_counts = {
        row["company_id"]: row["n"]
        for row in conn.execute(
            f"SELECT company_id, COUNT(*) AS n FROM roles WHERE company_id IN ({marks}) GROUP BY company_id",
            ids,
        )
    }
    contacts: dict[int, list] = {item: [] for item in ids}
    for row in conn.execute(
        f"SELECT * FROM contacts WHERE company_id IN ({marks}) ORDER BY auto_send DESC, id DESC",
        ids,
    ):
        contacts[row["company_id"]].append(
            {
                "id": row["id"],
                "name": row["name"],
                "title": row["title"],
                "email": row["email"],
                "kind": row["kind"],
                "source": row["source"],
                "auto_send": bool(row["auto_send"]),
            }
        )
    latest = {}
    for row in conn.execute(
        f"""
        SELECT o.* FROM outreach o
        WHERE o.id IN (
          SELECT MAX(id) FROM outreach WHERE company_id IN ({marks}) GROUP BY company_id
        )
        """,
        ids,
    ):
        latest[row["company_id"]] = {
            "status": row["status"],
            "subject": row["subject"],
            "error": row["error"] or "",
            "sent_at": row["sent_at"],
        }
    payload = []
    for row in companies:
        payload.append(
            {
                "id": row["id"],
                "name": row["name"],
                "website": row["website"] or "",
                "market": row["market"],
                "role_count": role_counts.get(row["id"], 0),
                "roles": roles.get(row["id"], []),
                "contacts": contacts.get(row["id"], []),
                "outreach": latest.get(row["id"]),
            }
        )
    return payload


@app.get("/api/overview")
def overview():
    with db() as conn:
        settings = get_settings(conn)
        sent_today = _sent_today(conn)
        sent_week = _sent_week(conn)
        sent_month = _sent_month(conn)

        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()[0])

        stats = {
            "companies": count("SELECT COUNT(*) FROM companies"),
            "local_companies": count("SELECT COUNT(*) FROM companies WHERE market='local'"),
            "international_companies": count("SELECT COUNT(*) FROM companies WHERE market='international'"),
            "both_companies": count("SELECT COUNT(*) FROM companies WHERE market='both'"),
            "local_roles": count("SELECT COUNT(*) FROM roles WHERE market='local'"),
            "international_roles": count("SELECT COUNT(*) FROM roles WHERE market='international'"),
            "contacts": count("SELECT COUNT(*) FROM contacts"),
            "sent": count("SELECT COUNT(*) FROM outreach WHERE status='sent'"),
            "queued": count("SELECT COUNT(*) FROM outreach WHERE status='queued'"),
            "replied": count("SELECT COUNT(*) FROM outreach WHERE status='replied'"),
            "failed": count("SELECT COUNT(*) FROM outreach WHERE status='failed'"),
            "sent_today": sent_today,
            "sent_week": sent_week,
            "sent_month": sent_month,
            "inbox_new": unseen_count(settings),
        }
        companies = _company_payload(conn)
        outreach = [
            {
                "id": row["id"],
                "company_id": row["company_id"],
                "company": row["company_name"],
                "to_email": row["to_email"],
                "subject": row["subject"],
                "body_text": row["body_text"],
                "status": row["status"],
                "error": row["error"] or "",
                "created_at": row["created_at"],
                "sent_at": row["sent_at"],
            }
            for row in conn.execute(
                """
                SELECT o.*, c.name AS company_name
                FROM outreach o JOIN companies c ON c.id = o.company_id
                ORDER BY o.id DESC LIMIT 60
                """
            )
        ]
        activity = [
            {"id": row["id"], "level": row["level"], "message": row["message"], "created_at": row["created_at"]}
            for row in conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 30")
        ]
        suppressions = [
            {"email": row["email"], "reason": row["reason"] or "", "created_at": row["created_at"]}
            for row in conn.execute("SELECT * FROM suppressions ORDER BY created_at DESC LIMIT 20")
        ]
        block = "" if settings.get("paused") == "1" else _send_block_reason(settings, sent_today)
    return {
        "agent": {
            "paused": settings.get("paused") == "1",
            "cycle_in_progress": controller.cycle_in_progress,
            "last_cycle_at": controller.last_cycle_at,
            "last_summary": controller.last_summary,
            "last_error": controller.last_error,
            "send_window_open": in_send_window(),
            "window_label": window_label(),
            "block_reason": block,
            "mailbox_ready": bool(settings.get("smtp_password")) and not settings.get("smtp_error"),
            "address_ready": bool((settings.get("postal_address") or "").strip()),
            "confirmed": settings.get("confirmed") == "1",
            "daily_cap": _cap(settings),
            "from_email": FROM_EMAIL,
            "adzuna_ready": bool(settings.get("adzuna_app_id") and settings.get("adzuna_app_key")),
        },
        "stats": stats,
        "companies": companies,
        "outreach": outreach,
        "activity": activity,
        "suppressions": suppressions,
        "settings": _public_settings(settings),
    }


@app.post("/api/settings")
async def update_settings(request: Request):
    payload = await request.json()
    updates = _apply_settings(payload)
    with db() as conn:
        set_settings(conn, updates)
        log(conn, "info", "Settings saved.")
    redraft_queued()
    paused = None
    with db() as conn:
        paused = get_settings(conn).get("paused") == "1"
    if paused:
        controller.last_summary = "The agent is paused. Hiring search and sending are both stopped."
    elif controller.request_cycle(force_discovery=False):
        pass
    return {"ok": True}


@app.post("/api/agent/run")
def run_cycle():
    started = controller.request_cycle(force_discovery=True)
    if not started:
        raise HTTPException(409, "A cycle is already running.")
    return {"ok": True}


@app.post("/api/mailbox/test")
def mailbox_test():
    with db() as conn:
        settings = get_settings(conn)
    try:
        send_test(settings)
    except MailboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db() as conn:
        set_settings(conn, {"smtp_error": ""})
        log(conn, "sent", f"Mailbox check delivered to {FROM_EMAIL}.")
    return {"ok": True}


@app.post("/api/companies/{company_id}/contacts")
async def add_contact(company_id: int, request: Request):
    payload = await request.json()
    found = extract_emails(str(payload.get("email") or ""))
    if len(found) != 1:
        raise HTTPException(400, "Enter one email address.")
    email = found[0]
    kind = classify_email(email)
    if kind == "skip":
        raise HTTPException(400, "That address cannot be used.")
    name = str(payload.get("name") or "").strip()[:120]
    title = str(payload.get("title") or "").strip()[:120] or (
        "Hiring contact" if kind == "named" else "Recruiting team"
    )
    if kind == "general" and name:
        kind = "named"
    with db() as conn:
        company = conn.execute("SELECT id FROM companies WHERE id=?", (company_id,)).fetchone()
        if not company:
            raise HTTPException(404, "Company not found.")
        conn.execute(
            "DELETE FROM outreach WHERE company_id=? AND status='queued'",
            (company_id,),
        )
        conn.execute(
            "INSERT INTO contacts (company_id, name, title, email, kind, source, auto_send, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'manual', 1, ?) "
            "ON CONFLICT(company_id, email) DO UPDATE SET "
            "name=excluded.name, title=excluded.title, kind=excluded.kind, source='manual', auto_send=1",
            (company_id, name, title, email, kind, now_iso()),
        )
        log(conn, "info", f"Contact {email} added for company {company_id}. The agent will send the proposal.")
    controller._queue_ready()
    return {"ok": True}


@app.post("/api/contacts/{contact_id}/allow")
def allow_contact(contact_id: int):
    with db() as conn:
        row = conn.execute("SELECT id, email FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Contact not found.")
        if unsuitable_address(row["email"]):
            raise HTTPException(400, "That address cannot be used.")
        conn.execute("UPDATE contacts SET auto_send=1 WHERE id=?", (contact_id,))
        log(conn, "info", f"The agent may email {row['email']}.")
    controller._queue_ready()
    return {"ok": True}


@app.post("/api/outreach/{outreach_id}/send")
def send_outreach(outreach_id: int):
    try:
        controller.send_one(outreach_id)
    except MailboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.post("/api/outreach/{outreach_id}/letter")
async def update_letter(outreach_id: int, request: Request):
    payload = await request.json()
    subject = str(payload.get("subject") or "").strip()
    body_text = str(payload.get("body_text") or "").strip()
    if not subject or not body_text:
        raise HTTPException(400, "Write a subject and a letter.")
    if len(subject) > 180 or len(body_text) > 20000:
        raise HTTPException(400, "That letter is too long.")
    with db() as conn:
        row = conn.execute("SELECT status FROM outreach WHERE id=?", (outreach_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Proposal not found.")
        if row["status"] not in {"queued", "failed"}:
            raise HTTPException(400, "A sent letter stays as it was sent.")
        conn.execute(
            "UPDATE outreach SET subject=?, body_text=?, body_html=?, edited=1, error='' WHERE id=?",
            (subject, body_text, render_plain(body_text), outreach_id),
        )
        log(conn, "info", "A proposal letter was edited.")
    return {"ok": True}


@app.get("/api/inbox")
def inbox():
    with db() as conn:
        settings = get_settings(conn)
    try:
        messages = list_inbox(settings)
    except MailboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"messages": messages}


@app.get("/api/inbox/{uid}")
def inbox_message(uid: str):
    with db() as conn:
        settings = get_settings(conn)
    try:
        return read_inbox(settings, uid)
    except MailboxError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/inbox/reply")
async def inbox_reply(request: Request):
    payload = await request.json()
    uid = str(payload.get("uid") or "")
    body = str(payload.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "Write a reply first.")
    if len(body) > 20000:
        raise HTTPException(400, "That reply is too long.")
    with db() as conn:
        settings = get_settings(conn)
    try:
        sent = send_inbox_reply(settings, uid, body)
    except MailboxError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db() as conn:
        log(conn, "sent", f"Reply sent to {sent['to']} from {FROM_EMAIL}.")
    return {"ok": True}


@app.api_route("/unsubscribe/{token}", methods=["GET", "POST"])
def unsubscribe(token: str, request: Request):
    with db() as conn:
        row = conn.execute(
            "SELECT id, to_email FROM outreach WHERE unsub_token=?", (token,)
        ).fetchone()
        if row:
            suppress(conn, row["to_email"], "unsubscribe link")
            conn.execute(
                "UPDATE outreach SET status='unsubscribed', error='' WHERE id=?",
                (row["id"],),
            )
            log(conn, "info", f"Stopped emailing {row['to_email']} from the unsubscribe link.")
    if request.method == "POST":
        return {"ok": True}
    page = """<!doctype html>
<html><head><meta charset="utf-8"><title>Unsubscribed</title></head>
<body style="font-family:Georgia,serif;background:#f3efe6;color:#10221e;padding:48px;">
<p>Talent Crafters will not email this address again.</p>
</body></html>"""
    return HTMLResponse(page)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
