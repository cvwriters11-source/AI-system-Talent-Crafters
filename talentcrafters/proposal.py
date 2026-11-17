"""Write the proposal that goes out from hr@talentcrafters.co.za."""

from __future__ import annotations

import html

from talentcrafters.db import FROM_EMAIL

DIRECTOR = "Samuel T"
DIRECTOR_TITLE = "Managing Director"
PHONE = "+27 74 650 2580"
TAGLINE = "Connecting Potential with Opportunity"


def _subject(title: str, location: str) -> str:
    line = f"RE: {title} – {location}" if location else f"RE: {title}"
    if len(line) <= 110:
        return line
    return line[:109].rstrip(" -–/") + "…"


def _article(title: str) -> str:
    return "an" if title[:1].lower() in "aeiou" else "a"


def _possessive(company: str) -> str:
    return company + ("'" if company.endswith(("s", "S")) else "'s")


def compose(
    *,
    company: str,
    title: str,
    location: str,
    market: str,
    extra_roles: int,
    contact_name: str,
    advantages: str,
    postal_address: str,
    unsub_url: str,
) -> tuple[str, str, str]:
    company = (company or "").strip()
    title = (title or "").strip() or "open role"
    place = (location or "").strip()
    where = f" based in {place}" if place else ""
    reference = f"{title} – {place}" if place else title
    address = (postal_address or "").strip() or "Leeuwpoort Street & Kaap St, CBD, Boksburg, 1460"
    subject = _subject(title, place)
    unsub = 'To stop these emails, reply with the subject "unsubscribe".'
    if unsub_url:
        unsub += f"\nOr use this link: {unsub_url}"
    # The approved letter uses the company and the role. These stay available for later wording.
    _ = (market, extra_roles, contact_name, advantages)

    body_text = f"""Dear {company},

RE: {reference}

We noticed that {company} is currently recruiting for {_article(title)} {title}{where}, and would like to introduce Talent Crafters Solutions as a potential recruitment partner for this position.

We partner with organisations to attract, identify, assess and secure talented professionals who meet their operational requirements and align with their organisational culture.

Our recruitment capabilities extend beyond a single local market. We recruit across South Africa and internationally, allowing us to identify suitable candidates across different talent markets depending on your hiring requirements.

OUR RECRUITMENT SERVICES

We can manage the recruitment process from initial job briefing through to candidate placement, including:

- Job analysis and consultation with your hiring manager.
- Candidate sourcing and talent mapping.
- CV screening and candidate assessments.
- Initial interviews and candidate verification.
- Submission of shortlisted candidates with CVs, interview summaries and availability.
- Interview scheduling and coordination.
- Offer management and onboarding support.

OUR DELIVERY TIMELINES

- First candidate shortlist: Within 10 working days of engagement.
- Typical recruitment cycle: 2–6 weeks, depending on the position and selection process.

RECRUITMENT FEES

We offer flexible recruitment arrangements:

- Contingency Recruitment: 10%–15% of the successful candidate's annual cost-to-company, payable upon placement.
- Retained Executive Search: Available for senior management and executive appointments.
- Volume Recruitment: Customised pricing for organisations with multiple vacancies.

Our fees are negotiable depending on the role, recruitment requirements and hiring volume.

WHY PARTNER WITH TALENT CRAFTERS SOLUTIONS?

- Access to a growing network of local and international professionals.
- Structured candidate screening and assessment before submission.
- A dedicated recruitment approach aligned with your hiring objectives.
- Fast turnaround, with an initial shortlist targeted within 10 working days.
- Flexible recruitment fees and payment arrangements.
- A replacement guarantee of 30–90 days, subject to the agreed terms and conditions.

We would appreciate the opportunity to introduce Talent Crafters Solutions and explore how we could support {_possessive(company)} current and future recruitment requirements.

If you are open to considering an additional recruitment partner, simply reply to this email and we will gladly share our company profile and recruitment proposal for your review.

{DIRECTOR}, {DIRECTOR_TITLE}, will be your primary point of contact.

We look forward to the possibility of working with {company}.

Kind regards,

{DIRECTOR}
{DIRECTOR_TITLE}
Talent Crafters Solutions

Email: {FROM_EMAIL}
Tel/WhatsApp: {PHONE}

{address}

You are receiving this because {company} has a role open and we are offering to recruit for it.
{unsub}
"""

    link_html = ""
    if unsub_url:
        safe = html.escape(unsub_url, quote=True)
        link_html = f'<br><a href="{safe}" style="color:#0d2240;">Unsubscribe</a>'
    address_html = "<br>".join(html.escape(line) for line in address.splitlines())
    heading = "margin:22px 0 8px;font-family:Georgia,serif;font-size:15px;letter-spacing:0.06em;color:#0d2240;"
    item = "margin:0 0 6px;"

    body_html = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f1ea;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f1ea;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border:1px solid #e4dcc8;">
<tr><td style="background:#0d2240;padding:22px 28px 18px;" align="center">
<img src="/static/logo.jpg" alt="Talent Crafters" width="320" style="display:block;width:320px;max-width:100%;height:auto;background:#ffffff;border-radius:6px;">
<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:13px;letter-spacing:0.08em;color:#e0c27a;">{html.escape(TAGLINE)}</p>
</td></tr>
<tr><td style="padding:8px 28px 24px;font-family:Calibri,sans-serif;font-size:16px;line-height:1.55;color:#1c2433;">
<p style="margin:18px 0 8px;">Dear {html.escape(company)},</p>
<p style="margin:0 0 14px;font-family:Georgia,serif;font-size:18px;color:#0d2240;">RE: {html.escape(reference)}</p>
<p style="margin:0 0 12px;">We noticed that {html.escape(company)} is currently recruiting for {_article(title)} {html.escape(title)}{html.escape(where)}, and would like to introduce Talent Crafters Solutions as a potential recruitment partner for this position.</p>
<p style="margin:0 0 12px;">We partner with organisations to attract, identify, assess and secure talented professionals who meet their operational requirements and align with their organisational culture.</p>
<p style="margin:0 0 12px;">Our recruitment capabilities extend beyond a single local market. We recruit across South Africa and internationally, allowing us to identify suitable candidates across different talent markets depending on your hiring requirements.</p>
<p style="{heading}">OUR RECRUITMENT SERVICES</p>
<p style="margin:0 0 8px;">We can manage the recruitment process from initial job briefing through to candidate placement, including:</p>
<ul style="margin:0 0 12px;padding-left:18px;">
<li style="{item}">Job analysis and consultation with your hiring manager.</li>
<li style="{item}">Candidate sourcing and talent mapping.</li>
<li style="{item}">CV screening and candidate assessments.</li>
<li style="{item}">Initial interviews and candidate verification.</li>
<li style="{item}">Submission of shortlisted candidates with CVs, interview summaries and availability.</li>
<li style="{item}">Interview scheduling and coordination.</li>
<li style="{item}">Offer management and onboarding support.</li>
</ul>
<p style="{heading}">OUR DELIVERY TIMELINES</p>
<ul style="margin:0 0 12px;padding-left:18px;">
<li style="{item}">First candidate shortlist: Within 10 working days of engagement.</li>
<li style="{item}">Typical recruitment cycle: 2–6 weeks, depending on the position and selection process.</li>
</ul>
<p style="{heading}">RECRUITMENT FEES</p>
<p style="margin:0 0 8px;">We offer flexible recruitment arrangements:</p>
<ul style="margin:0 0 12px;padding-left:18px;">
<li style="{item}">Contingency Recruitment: 10%–15% of the successful candidate's annual cost-to-company, payable upon placement.</li>
<li style="{item}">Retained Executive Search: Available for senior management and executive appointments.</li>
<li style="{item}">Volume Recruitment: Customised pricing for organisations with multiple vacancies.</li>
</ul>
<p style="margin:0 0 12px;">Our fees are negotiable depending on the role, recruitment requirements and hiring volume.</p>
<p style="{heading}">WHY PARTNER WITH TALENT CRAFTERS SOLUTIONS?</p>
<ul style="margin:0 0 12px;padding-left:18px;">
<li style="{item}">Access to a growing network of local and international professionals.</li>
<li style="{item}">Structured candidate screening and assessment before submission.</li>
<li style="{item}">A dedicated recruitment approach aligned with your hiring objectives.</li>
<li style="{item}">Fast turnaround, with an initial shortlist targeted within 10 working days.</li>
<li style="{item}">Flexible recruitment fees and payment arrangements.</li>
<li style="{item}">A replacement guarantee of 30–90 days, subject to the agreed terms and conditions.</li>
</ul>
<p style="margin:0 0 12px;">We would appreciate the opportunity to introduce Talent Crafters Solutions and explore how we could support {html.escape(_possessive(company))} current and future recruitment requirements.</p>
<p style="margin:0 0 12px;">If you are open to considering an additional recruitment partner, simply reply to this email and we will gladly share our company profile and recruitment proposal for your review.</p>
<p style="margin:0 0 12px;">{html.escape(DIRECTOR)}, {html.escape(DIRECTOR_TITLE)}, will be your primary point of contact.</p>
<p style="margin:0 0 16px;">We look forward to the possibility of working with {html.escape(company)}.</p>
<p style="margin:0 0 12px;">Kind regards,</p>
<p style="margin:0;font-family:Georgia,serif;font-size:18px;color:#0d2240;">{html.escape(DIRECTOR)}</p>
<p style="margin:2px 0 0;color:#5c6570;">{html.escape(DIRECTOR_TITLE)}<br>Talent Crafters Solutions</p>
<p style="margin:8px 0 0;">Email: <a href="mailto:{FROM_EMAIL}" style="color:#0d2240;">{FROM_EMAIL}</a><br>Tel/WhatsApp: {html.escape(PHONE)}</p>
</td></tr>
<tr><td style="padding:0 28px 22px;font-family:Calibri,sans-serif;font-size:12px;line-height:1.5;color:#5c6570;">
{address_html}<br><br>
You are receiving this because {html.escape(company)} has a role open and we are offering to recruit for it.<br>
To stop these emails, reply with the subject &quot;unsubscribe&quot;.{link_html}
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
    return subject, body_text, body_html


def render_plain(body_text: str) -> str:
    """Brand a letter someone rewrote, without putting the old wording back."""
    blocks = []
    for block in (body_text or "").split("\n\n"):
        lines = "<br>".join(html.escape(line) for line in block.split("\n"))
        if lines.strip():
            blocks.append(f"<p style='margin:0 0 12px;'>{lines}</p>")
    body = "".join(blocks) or "<p></p>"
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f1ea;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f1ea;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;background:#ffffff;border:1px solid #e4dcc8;">
<tr><td style="background:#0d2240;padding:22px 28px 18px;" align="center">
<img src="/static/logo.jpg" alt="Talent Crafters" width="320" style="display:block;width:320px;max-width:100%;height:auto;background:#ffffff;border-radius:6px;">
<p style="margin:12px 0 0;font-family:Georgia,serif;font-size:13px;letter-spacing:0.08em;color:#e0c27a;">{html.escape(TAGLINE)}</p>
</td></tr>
<tr><td style="padding:24px 28px 28px;font-family:Calibri,sans-serif;font-size:16px;line-height:1.55;color:#1c2433;">
{body}
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""
