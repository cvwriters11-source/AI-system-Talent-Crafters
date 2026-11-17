"""Pick a published recruiting contact. Addresses are never guessed."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from talentcrafters.discover import HEADERS, html_to_text

EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

SKIP_LOCAL_PREFIXES = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")
SKIP_LOCAL = {
    "privacy",
    "abuse",
    "postmaster",
    "webmaster",
    "bounce",
    "dpo",
    "legal",
    "security",
    "errors",
    "error",
}
GENERIC = {
    "info",
    "hello",
    "contact",
    "enquiries",
    "enquiry",
    "inquiries",
    "inquiry",
    "office",
    "admin",
    "sales",
    "support",
    "accounts",
    "finance",
    "marketing",
    "press",
    "media",
    "general",
    "help",
    "service",
    "billing",
    "customerservice",
    "reception",
}
RECRUITING = {
    "hr",
    "humanresources",
    "talent",
    "talentacquisition",
    "careers",
    "career",
    "jobs",
    "job",
    "recruit",
    "recruiting",
    "recruitment",
    "recruiter",
    "people",
    "hiring",
    "apply",
    "applications",
    "application",
    "cv",
    "cvs",
    "resumes",
    "resume",
    "vacancies",
    "vacancy",
    "workwithus",
    "joinus",
    "join",
}
SKIP_DOMAINS = {
    "example.com",
    "email.com",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "schema.org",
    "mysite.com",
    "w3.org",
    "www.w3.org",
    "schema.org",
    "remoteok.com",
    "remotive.com",
    "arbeitnow.com",
    "jobicy.com",
    "adzuna.com",
    "themuse.com",
    "talentcrafters.co.za",
    "sentry.io",
}
SKIP_HOST_PARTS = (
    "remoteok.com",
    "remotive.com",
    "arbeitnow.com",
    "jobicy.com",
    "adzuna.",
    "indeed.",
    "linkedin.",
    "facebook.",
    "instagram.",
    "twitter.",
    "x.com",
    "youtube.",
    "google.",
    "glassdoor.",
    "greenhouse.io",
    "lever.co",
    "workable.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "successfactors.",
    "recruitee.com",
    "bamboohr.com",
    "ashbyhq.com",
    "teamtailor.com",
    "wikipedia.org",
    "schema.org",
    "w3.org",
    "workable.com",
    "gstatic.",
    "googleapis.",
    "cloudflare.",
    "doubleclick.",
    "googletagmanager.",
    "gravatar.",
    "wp.com",
    "wix.com",
)
STOP_NAMES = {
    "South Africa",
    "Cape Town",
    "Port Elizabeth",
    "East London",
    "Human Resources",
    "Talent Acquisition",
    "Hiring Manager",
    "Thank You",
    "Kind Regards",
    "Best Regards",
    "United States",
    "United Kingdom",
    "New York",
    "Los Angeles",
    "San Francisco",
    "Remote Ok",
}


def unsuitable_address(email: str) -> bool:
    domain = email.split("@")[-1].lower()
    blocked = ("sentry.io", "w3.org", "schema.org", "example.com")
    return any(domain == item or domain.endswith("." + item) for item in blocked)


def classify_email(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    if local in SKIP_LOCAL or local.startswith(SKIP_LOCAL_PREFIXES):
        return "skip"
    tokens = [token for token in re.split(r"[._+\-]+", local) if token]
    if any(token in RECRUITING for token in tokens):
        return "recruiting_inbox"
    if len(local) < 3:
        return "skip"
    if tokens and all(token in GENERIC for token in tokens):
        return "general"
    return "named"


def extract_emails(*chunks: str) -> list[str]:
    blob = "\n".join(chunk or "" for chunk in chunks)
    found = EMAIL_RE.findall(blob)
    found += re.findall(r"mailto:([^?\"'>\s]+)", blob, flags=re.I)
    unique: list[str] = []
    seen: set[str] = set()
    for raw in found:
        email = raw.strip(" .,;:<>()[]{}").lower()
        if email in seen or not _usable(email):
            continue
        if classify_email(email) == "skip":
            continue
        seen.add(email)
        unique.append(email)
    return unique


def _usable(email: str) -> bool:
    if email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if "." not in domain or len(domain) > 80 or len(local) > 64 or len(local) < 2:
        return False
    if "u003e" in local or "u003e" in domain:
        return False
    if domain in SKIP_DOMAINS or domain.endswith(".talentcrafters.co.za"):
        return False
    parts = domain.split(".")
    if any(part in {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js"} for part in parts):
        return False
    return True


def guess_website(*chunks: str) -> str:
    for chunk in chunks:
        for raw in URL_RE.findall(chunk or ""):
            raw = raw.rstrip(").,;]}")
            try:
                parsed = urlparse(raw)
            except ValueError:
                continue
            host = (parsed.hostname or "").lower()
            if not host or any(part in host for part in SKIP_HOST_PARTS):
                continue
            if host.replace(".", "").isdigit():
                continue
            scheme = "https" if parsed.scheme != "http" else "https"
            return f"{scheme}://{host}"
    return ""


def _name_near(text: str, email: str) -> str:
    plain = html_to_text(text)
    index = plain.lower().find(email.lower())
    if index < 0:
        return ""
    window = plain[max(0, index - 80) : index]
    matches = NAME_RE.findall(window)
    if not matches:
        return ""
    candidate = matches[-1].strip()
    if candidate in STOP_NAMES:
        return ""
    return candidate


def harvest(blob: str, source: str) -> list[dict]:
    contacts: list[dict] = []
    for email in extract_emails(blob):
        kind = classify_email(email)
        if kind == "skip":
            continue
        auto = 1 if source in {"job_post", "company_site"} else 0
        name = _name_near(blob, email) if kind == "named" else ""
        if kind == "recruiting_inbox":
            title = "Recruiting team"
        elif kind == "named":
            title = "Hiring contact"
        else:
            title = "General inbox"
        contacts.append(
            {
                "name": name,
                "title": title,
                "email": email,
                "kind": kind,
                "source": source,
                "auto_send": auto,
            }
        )
    return contacts


def _best(contacts: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for contact in contacts:
        current = deduped.get(contact["email"])
        if current is None or (contact["auto_send"], contact["kind"] == "named") > (
            current["auto_send"],
            current["kind"] == "named",
        ):
            deduped[contact["email"]] = contact
    ranked = sorted(
        deduped.values(),
        key=lambda item: (
            item["auto_send"],
            item["kind"] == "named",
            item["kind"] == "recruiting_inbox",
        ),
        reverse=True,
    )
    if not ranked:
        return []
    if ranked[0]["auto_send"]:
        return [item for item in ranked if item["auto_send"]][:1]
    return ranked[:1]


def _page_text(client: httpx.Client, url: str) -> str:
    try:
        response = client.get(url, headers={**HEADERS, "Accept": "text/html"})
    except httpx.HTTPError:
        return ""
    if response.status_code >= 400:
        return ""
    kind = response.headers.get("content-type", "")
    if kind and "html" not in kind and "text" not in kind:
        return ""
    return response.text[:400_000]


def find_public_contacts(
    client: httpx.Client, website: str, roles: list[dict]
) -> tuple[str, list[dict]]:
    """Return a website and at most one published contact."""
    blobs = [role.get("description") or "" for role in roles]
    urls = [role.get("url") or "" for role in roles]
    found = harvest("\n".join(blobs), "job_post")
    site = website or guess_website(*blobs, *urls)
    best = _best(found)
    if best and best[0]["auto_send"]:
        return site, best

    for url in urls[:1]:
        if not url.startswith("http"):
            continue
        page = _page_text(client, url)
        if page:
            found.extend(harvest(page, "job_post"))
            site = site or guess_website(page)
            best = _best(found)
            if best and best[0]["auto_send"]:
                return site, best

    if site:
        for path in ("", "/contact", "/contact-us", "/careers"):
            page = _page_text(client, site.rstrip("/") + path)
            if not page:
                continue
            found.extend(harvest(page, "company_site"))
            best = _best(found)
            if best and best[0]["auto_send"]:
                return site, best
    return site, _best(found)


def hunter_contacts(client: httpx.Client, domain: str, api_key: str, auto_send: bool) -> list[dict]:
    if not domain or not api_key:
        return []
    try:
        response = client.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": api_key, "limit": 5, "department": "hr"},
        )
        response.raise_for_status()
        emails = (response.json().get("data") or {}).get("emails") or []
    except (httpx.HTTPError, ValueError):
        return []
    contacts: list[dict] = []
    for item in emails:
        email = (item.get("value") or "").lower()
        if not _usable(email):
            continue
        position = item.get("position") or "People leader"
        position_l = position.lower()
        if not any(word in position_l for word in ("talent", "recruit", "people", "human", "hr", "hiring")):
            continue
        first = item.get("first_name") or ""
        last = item.get("last_name") or ""
        contacts.append(
            {
                "name": " ".join(part for part in (first, last) if part),
                "title": position,
                "email": email,
                "kind": "named",
                "source": "hunter",
                "auto_send": 1 if auto_send else 0,
            }
        )
        if len(contacts) >= 1:
            break
    return contacts
