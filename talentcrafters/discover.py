"""Find open roles on public job boards and mark South African ones as local."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlparse

import httpx

HEADERS = {
    "User-Agent": "TalentCraftersOutreach/1.0 (hr@talentcrafters.co.za)",
    "Accept": "application/json, text/html;q=0.8",
}

ZA_MARKERS = (
    "south africa",
    "south-africa",
    "johannesburg",
    "cape town",
    "durban",
    "pretoria",
    "gqeberha",
    "port elizabeth",
    "bloemfontein",
    "polokwane",
    "nelspruit",
    "mbombela",
    "stellenbosch",
    "sandton",
    "midrand",
    "centurion",
    "soweto",
    "pietermaritzburg",
    "rustenburg",
    "kimberley",
    "east london",
    "potchefstroom",
    "emalahleni",
    "randburg",
    "roodepoort",
    "umhlanga",
    "bellville",
    "paarl",
    "knysna",
    "hermanus",
    "polokwane",
    "witbank",
    "tshwane",
    "ekurhuleni",
)

SKIP_COMPANIES = {
    "remote ok",
    "remoteok",
    "remotive",
    "arbeitnow",
    "jobicy",
    "adzuna",
    "confidential",
    "unnamed",
    "n a",
    "private",
    "hiring company",
}


def clean_site(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    blocked = (
        "schema.org",
        "w3.org",
        "workable.com",
        "example.com",
        "remoteok.com",
        "remotive.com",
        "arbeitnow.com",
        "jobicy.com",
    )
    if not host or any(part in host for part in blocked):
        return ""
    return url.split("#")[0][:300]


def _keep_links(raw: str) -> str:
    """Keep mailto addresses and hrefs after tags are stripped, so contact matching still sees them."""
    raw = raw or ""
    plain = html_to_text(raw)[:2000]
    extras = re.findall(r"""href=["']([^"']+)["']""", raw, flags=re.I)
    extras += re.findall(r"mailto:([^?\"'>\s]+)", raw, flags=re.I)
    kept = []
    seen = set()
    for item in extras:
        item = item.strip()
        if not item or item in seen or item in plain:
            continue
        seen.add(item)
        kept.append(item)
        if len(kept) >= 20:
            break
    if kept:
        plain = f"{plain}\n{' '.join(kept)}"
    return plain[:3200]


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clip(value: str, limit: int) -> str:
    if isinstance(value, list):
        value = ", ".join(str(part) for part in value)
    value = " ".join(str(value or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def classify_market(location: str) -> str:
    loc = (location or "").lower()
    if any(marker in loc for marker in ZA_MARKERS):
        return "local"
    return "international"


def name_key(name: str) -> str:
    text = (name or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(
        r"\b(pty|ltd|limited|inc|llc|gmbh|corp|corporation|co)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()


def _clean_company(name: str) -> str:
    return clip(name or "", 180)


def normalize(
    *,
    company: str,
    title: str,
    location: str,
    url: str,
    source: str,
    description: str,
    market: str | None = None,
    website: str = "",
) -> dict | None:
    company = _clean_company(company)
    title = clip(title or "", 180)
    location = clip(location or "", 180)
    if len(company) < 2 or len(title) < 2:
        return None
    if name_key(company) in SKIP_COMPANIES:
        return None
    plain = _keep_links(description or "")
    return {
        "company": company,
        "title": title,
        "location": location,
        "url": (url or "").strip()[:500],
        "source": source,
        "description": plain,
        "market": market or classify_market(location),
        "website": clean_site(website),
    }


def _get_json(client: httpx.Client, url: str, params: dict | None = None):
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()


def fetch_remotive(client: httpx.Client) -> list[dict]:
    jobs: list[dict] = []
    queries = (
        {"limit": 50},
        {"search": "south africa", "limit": 30},
    )
    seen: set[str] = set()
    for params in queries:
        payload = _get_json(client, "https://remotive.com/api/remote-jobs", params)
        for item in (payload.get("jobs") or [])[:40]:
            key = str(item.get("id") or item.get("url") or "")
            if key and key in seen:
                continue
            seen.add(key)
            job = normalize(
                company=item.get("company_name") or "",
                title=item.get("title") or "",
                location=item.get("candidate_required_location") or "",
                url=item.get("url") or "",
                source="Remotive",
                description=item.get("description") or "",
            )
            if job:
                jobs.append(job)
    return jobs


def fetch_remoteok(client: httpx.Client) -> list[dict]:
    payload = _get_json(client, "https://remoteok.com/api")
    jobs: list[dict] = []
    if not isinstance(payload, list):
        return jobs
    for item in payload:
        if not isinstance(item, dict) or "position" not in item:
            continue
        job = normalize(
            company=item.get("company") or "",
            title=item.get("position") or "",
            location=item.get("location") or "",
            url=item.get("url") or item.get("apply_url") or "",
            source="Remote OK",
            description=item.get("description") or "",
        )
        if job:
            jobs.append(job)
        if len(jobs) >= 40:
            break
    return jobs


def fetch_arbeitnow(client: httpx.Client) -> list[dict]:
    payload = _get_json(client, "https://www.arbeitnow.com/api/job-board-api")
    jobs: list[dict] = []
    for item in (payload.get("data") or [])[:40]:
        job = normalize(
            company=item.get("company_name") or "",
            title=item.get("title") or "",
            location=item.get("location") or "",
            url=item.get("url") or "",
            source="Arbeitnow",
            description=item.get("description") or "",
        )
        if job:
            jobs.append(job)
    return jobs


def fetch_jobicy(client: httpx.Client) -> list[dict]:
    jobs: list[dict] = []
    seen: set[str] = set()
    for params in ({"count": 40}, {"count": 20, "geo": "south-africa"}):
        try:
            payload = _get_json(client, "https://jobicy.com/api/v2/remote-jobs", params)
        except (httpx.HTTPError, ValueError):
            continue
        for item in payload.get("jobs") or []:
            key = str(item.get("id") or "")
            if key and key in seen:
                continue
            seen.add(key)
            geos = item.get("jobGeo") or ""
            if isinstance(geos, list):
                geos = ", ".join(str(part) for part in geos)
            job = normalize(
                company=item.get("companyName") or "",
                title=item.get("jobTitle") or "",
                location=str(geos),
                url=item.get("url") or "",
                source="Jobicy",
                description=item.get("jobDescription") or item.get("jobExcerpt") or "",
            )
            if job:
                jobs.append(job)
    return jobs


def fetch_adzuna(client: httpx.Client, app_id: str, app_key: str) -> list[dict]:
    if not app_id or not app_key:
        return []
    jobs: list[dict] = []
    for country in ("za", "gb", "us", "au"):
        payload = _get_json(
            client,
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
            {
                "app_id": app_id,
                "app_key": app_key,
                "results_per_page": 25,
            },
        )
        for item in payload.get("results") or []:
            company = ((item.get("company") or {}).get("display_name")) or ""
            location = ((item.get("location") or {}).get("display_name")) or ""
            market = "local" if country == "za" else None
            if country == "za" and "south africa" not in location.lower():
                location = f"{location}, South Africa".strip(", ")
            job = normalize(
                company=company,
                title=item.get("title") or "",
                location=location,
                url=item.get("redirect_url") or "",
                source="Adzuna",
                description=item.get("description") or "",
                market=market,
            )
            if job:
                jobs.append(job)
    return jobs


def fetch_workable(client: httpx.Client, page_token: str) -> tuple[list[dict], str]:
    """South African roles, paging forward, plus a page of United Kingdom roles."""

    def load(location: str, market: str | None, token: str) -> tuple[list[dict], str]:
        params = {"location": location}
        if token:
            params["pageToken"] = token
        payload = _get_json(client, "https://jobs.workable.com/api/v1/jobs", params)
        jobs: list[dict] = []
        for item in (payload.get("jobs") or [])[:20]:
            company = item.get("company") or {}
            loc = item.get("location") or {}
            city = loc.get("city") or "" if isinstance(loc, dict) else ""
            country = loc.get("countryName") or "" if isinstance(loc, dict) else ""
            place = ", ".join(part for part in (city, country) if part) or location
            forced = market
            if "south africa" in place.lower():
                forced = "local"
            description = " ".join(
                part or "" for part in (item.get("description"), item.get("requirementsSection"))
            )
            job = normalize(
                company=company.get("title") or "",
                title=item.get("title") or "",
                location=place,
                url=item.get("url") or "",
                source="Workable",
                description=description,
                market=forced,
                website=company.get("website") or "",
            )
            if job:
                jobs.append(job)
        return jobs, payload.get("nextPageToken") or ""

    try:
        local_jobs, next_token = load("South Africa", "local", page_token)
    except (httpx.HTTPError, ValueError):
        if not page_token:
            raise
        local_jobs, next_token = load("South Africa", "local", "")
    jobs = list(local_jobs)
    try:
        international, _token = load("United Kingdom", None, "")
        jobs.extend(international)
    except (httpx.HTTPError, ValueError):
        pass
    return jobs, next_token


def collect(client: httpx.Client, settings: dict[str, str]) -> tuple[list[dict], list[str], dict[str, str]]:
    found: list[dict] = []
    notes: list[str] = []
    updates: dict[str, str] = {}
    try:
        workable, token = fetch_workable(client, settings.get("workable_sa_token", ""))
        found.extend(workable)
        notes.append(f"Workable: {len(workable)} roles")
        updates["workable_sa_token"] = token
    except Exception as exc:
        notes.append(f"Workable unavailable ({type(exc).__name__})")
    sources = (
        ("Remotive", lambda: fetch_remotive(client)),
        ("Remote OK", lambda: fetch_remoteok(client)),
        ("Arbeitnow", lambda: fetch_arbeitnow(client)),
        ("Jobicy", lambda: fetch_jobicy(client)),
        (
            "Adzuna",
            lambda: fetch_adzuna(
                client, settings.get("adzuna_app_id", ""), settings.get("adzuna_app_key", "")
            ),
        ),
    )
    for name, fetch in sources:
        if name == "Adzuna" and not (
            settings.get("adzuna_app_id") and settings.get("adzuna_app_key")
        ):
            continue
        try:
            batch = fetch()
            found.extend(batch)
            notes.append(f"{name}: {len(batch)} roles")
        except Exception as exc:
            notes.append(f"{name} unavailable ({type(exc).__name__})")
    return found, notes, updates
