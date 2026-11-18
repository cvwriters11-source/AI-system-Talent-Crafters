const TITLES = {
  overview: ["Dashboard", "Emails sent today, this week, and this month, plus the companies the agent has found."],
  companies: ["Companies", "Open roles the agent has found, with the decision-maker address when one was published."],
  proposals: ["Proposals", "Letters written in the voice of Talent Crafters, ready to leave from hr@talentcrafters.co.za."],
  inbox: ["Inbox", "Mail that has arrived at hr@talentcrafters.co.za. Open one and reply from here."],
  agent: ["Agent", "The search runs on its own. Letters wait in the queue and go out at 16:00 SAST."],
};

const panel = document.getElementById("panel");
const title = document.getElementById("title");
const lede = document.getElementById("lede");
let tab = "overview";
let data = null;
let marketFilter = "all";
let proposalFilter = "all";
let selectedProposal = null;
let shownLetter = null;
let letterEditing = false;
let formReady = false;
let inboxItems = [];
let selectedMail = null;

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmt(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-ZA", {
    timeZone: "Africa/Johannesburg",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function pillState(agent) {
  if (agent.paused) return ["Paused", "paused"];
  if (agent.cycle_in_progress) return ["Searching", "busy"];
  if (!agent.mailbox_ready || !agent.address_ready || !agent.confirmed) return ["Collecting", "wait"];
  return ["Sending live", "live"];
}

function renderChrome() {
  if (!data) return;
  const [label, kind] = pillState(data.agent);
  const pill = document.getElementById("pill");
  pill.textContent = label;
  pill.className = `pill ${kind}`;
  document.getElementById("pause").textContent = data.agent.paused ? "Resume agent" : "Pause agent";
  document.getElementById("count-companies").textContent = data.stats.companies || "";
  document.getElementById("count-proposals").textContent = data.stats.queued + data.stats.sent || "";
  const stats = data.stats;
  document.getElementById("stat-companies").textContent = stats.companies;
  document.getElementById("stat-local").textContent = stats.local_roles;
  document.getElementById("stat-international").textContent = stats.international_roles;
  document.getElementById("stat-contacts").textContent = stats.contacts;
  document.getElementById("stat-sent").textContent = stats.sent;
  document.getElementById("stat-queued").textContent = stats.queued;
  const inboxNew = Number(stats.inbox_new || 0);
  const inboxCount = document.getElementById("count-inbox");
  inboxCount.textContent = String(inboxNew);
  inboxCount.className = inboxNew ? "nav-count new" : "nav-count";
}

function banner() {
  const agent = data.agent;
  if (agent.paused) {
    return `<div class="banner">The agent is paused. Resume it and the search continues on its own.</div>`;
  }
  if (agent.block_reason) {
    return `<div class="banner">${esc(agent.block_reason)}</div>`;
  }
  return "";
}

function activityList(items) {
  if (!items.length) return `<p class="empty">The log will fill as the agent works.</p>`;
  return `<ul class="feed">${items.map((item) => `
    <li class="level-${esc(item.level)}">${esc(item.message)}<time>${esc(fmt(item.created_at))} SAST</time></li>
  `).join("")}</ul>`;
}

function fillOverview() {
  const stats = data.stats;
  const totalRoles = stats.local_roles + stats.international_roles;
  const localWidth = totalRoles ? Math.round((stats.local_roles / totalRoles) * 100) : 0;
  document.getElementById("banner-slot").innerHTML = banner();
  document.getElementById("sent-day").textContent = stats.sent_today;
  document.getElementById("sent-week").textContent = stats.sent_week;
  document.getElementById("sent-month").textContent = stats.sent_month;
  document.getElementById("mix").innerHTML = `
    <div class="bar"><span class="local" style="width:${localWidth}%"></span><span class="intl" style="width:${100 - localWidth}%"></span></div>
    <div class="split-label"><span>${stats.local_roles} local</span><span>${stats.international_roles} international</span></div>
    <p class="meta" style="margin-top:12px">${stats.local_companies} local companies · ${stats.international_companies} international · ${stats.both_companies} hiring in both</p>`;
  document.getElementById("summary-line").textContent = data.agent.last_summary || "";
  document.getElementById("cycle-line").textContent = data.agent.last_cycle_at
    ? `Last cycle ${fmt(data.agent.last_cycle_at)} SAST · ${data.agent.window_label}`
    : data.agent.window_label;
  const recent = data.companies.slice(0, 6);
  document.getElementById("recent-companies").innerHTML = recent.length
    ? `<table><tbody>${recent.map(companyRow).join("")}</tbody></table>`
    : `<p class="empty">The first search is still reaching the job boards.</p>`;
  document.getElementById("overview-feed").innerHTML = activityList(data.activity.slice(0, 8));
  const adzuna = document.getElementById("adzuna-note");
  adzuna.hidden = data.agent.adzuna_ready;
}

function companyRow(company) {
  const contact = company.contacts[0];
  const roles = company.roles.map((role) => {
    const where = role.location ? ` · ${esc(role.location)}` : "";
    const link = role.url && role.url.startsWith("http")
      ? `<a href="${esc(role.url)}" target="_blank" rel="noreferrer">${esc(role.title)}</a>`
      : esc(role.title);
    return `<span class="role">${link}${where}</span>`;
  }).join("");
  let contactCell = `<span class="who">No published address yet</span>`;
  if (contact) {
    const who = contact.name || contact.title || "Contact";
    contactCell = `<div>${esc(who)}</div><div class="who">${esc(contact.email)}</div>`;
    if (!contact.auto_send) {
      contactCell += `<div class="held">Held until you allow sending</div>
        <button type="button" class="tiny" data-allow="${contact.id}">Allow sending</button>`;
    }
  } else {
    contactCell += `<div><button type="button" class="tiny" data-add="${company.id}" data-name="${esc(company.name)}">Add email</button></div>`;
  }
  const status = company.outreach
    ? `<div class="status ${esc(company.outreach.status)}">${esc(company.outreach.status)}</div>`
    : "";
  return `<tr>
    <td><div class="company-name">${esc(company.name)}</div>${roles}</td>
    <td><span class="badge ${esc(company.market)}">${esc(company.market)}</span></td>
    <td>${contactCell}</td>
    <td>${status}</td>
  </tr>`;
}

function fillCompanies() {
  const query = (document.getElementById("company-search").value || "").trim().toLowerCase();
  const rows = data.companies.filter((company) => {
    if (marketFilter === "contacts" && !company.contacts.length) return false;
    if (marketFilter === "local" && company.market === "international") return false;
    if (marketFilter === "international" && company.market === "local") return false;
    if (!query) return true;
    const blob = `${company.name} ${company.roles.map((role) => role.title).join(" ")} ${(company.contacts[0] || {}).email || ""}`.toLowerCase();
    return blob.includes(query);
  });
  document.getElementById("company-table").innerHTML = rows.length
    ? `<table><thead><tr><th>Company and open roles</th><th>Market</th><th>Decision maker</th><th>Proposal</th></tr></thead><tbody>${rows.map(companyRow).join("")}</tbody></table>`
    : `<p class="empty">No companies in this view yet.</p>`;
}

function sizeLetter(frame) {
  const fit = () => {
    const doc = frame.contentDocument;
    if (!doc || !doc.body) return;
    const height = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight);
    frame.style.height = `${height + 4}px`;
  };
  frame.onload = fit;
  if (frame.contentDocument && frame.contentDocument.readyState === "complete") fit();
}

function fillProposals() {
  const list = document.getElementById("proposal-list");
  const frame = document.getElementById("letter-frame");
  const editor = document.getElementById("letter-edit");
  const editButton = document.getElementById("edit-letter");
  const visible = data.outreach.filter((item) => proposalFilter === "all" || item.status === proposalFilter);
  const filterLabel = {
    all: "All letters",
    queued: "Queued. These go out at 16:00 SAST.",
    sent: "Sent proposals",
  }[proposalFilter] || "All letters";
  if (!visible.length) {
    list.innerHTML = `<p class="meta">${esc(filterLabel)}</p><p class="empty">Nothing in this list yet.</p>`;
    shownLetter = null;
    letterEditing = false;
    editor.hidden = true;
    editButton.hidden = true;
    frame.removeAttribute("src");
    frame.srcdoc = "<p style='font-family:Georgia,serif;color:#0d2240;padding:24px;'>The company letter will show here.</p>";
    return;
  }
  if (!visible.some((item) => item.id === selectedProposal)) {
    selectedProposal = visible[0].id;
  }
  const current = visible.find((item) => item.id === selectedProposal);
  const markup = `<p class="meta">${esc(filterLabel)}</p>` + visible.map((item) => `
    <div class="proposal-row ${item.id === selectedProposal ? "active" : ""}">
      <button type="button" class="proposal-open" data-proposal="${item.id}">
        <strong>${esc(item.subject)}</strong>
        <span class="who">${esc(item.company)} · ${esc(item.to_email)}</span>
        <span class="status ${esc(item.status)}">${esc(item.status)}${item.sent_at ? " · " + esc(fmt(item.sent_at)) : ""}</span>
        ${item.error ? `<span class="error-text">${esc(item.error)}</span>` : ""}
      </button>
      ${item.status === "queued" || item.status === "failed" ? `<button type="button" class="tiny send-now" data-send="${item.id}">Send this email</button>` : ""}
    </div>`).join("");
  if (list.innerHTML !== markup) list.innerHTML = markup;
  document.getElementById("letter-subject-line").textContent = current.subject;
  const canEdit = current.status === "queued" || current.status === "failed";
  editButton.hidden = !canEdit;
  if (shownLetter !== selectedProposal) {
    letterEditing = false;
    editor.hidden = true;
    sizeLetter(frame);
    frame.removeAttribute("srcdoc");
    frame.src = `/letters/${selectedProposal}?v=${Date.now()}`;
    shownLetter = selectedProposal;
  }
}

function fillAgent() {
  document.getElementById("agent-summary").textContent = data.agent.last_summary || "";
  document.getElementById("agent-window").textContent = `${data.agent.window_label} Sent today ${data.stats.sent_today}.`;
  document.getElementById("agent-feed").innerHTML = activityList(data.activity);
  const stopped = data.suppressions.length
    ? data.suppressions.map((item) => `<li>${esc(item.email)} <span class="who">${esc(item.reason)}</span></li>`).join("")
    : `<li class="who">None yet.</li>`;
  document.getElementById("stop-list").innerHTML = stopped;
  const form = document.getElementById("settings-form");
  const editing = document.activeElement && form.contains(document.activeElement);
  if (formReady || editing) return;
  const settings = data.settings;
  form.smtp_host.value = settings.smtp_host;
  form.smtp_port.value = settings.smtp_port;
  form.smtp_user.value = settings.smtp_user;
  form.imap_host.value = settings.imap_host;
  form.imap_port.value = settings.imap_port;
  form.postal_address.value = settings.postal_address;
  form.daily_cap.value = settings.daily_cap;
  form.advantages.value = settings.advantages;
  form.public_base_url.value = settings.public_base_url;
  form.confirmed.checked = settings.confirmed;
  form.hunter_auto_send.checked = settings.hunter_auto_send;
  form.smtp_password.placeholder = settings.smtp_password_set ? "Saved — leave blank to keep" : "Mailbox password or app password";
  form.hunter_api_key.placeholder = settings.hunter_api_key_set ? "Saved — leave blank to keep" : "Optional";
  form.adzuna_app_id.placeholder = settings.adzuna_app_id_set ? "Saved — leave blank to keep" : "Optional";
  form.adzuna_app_key.placeholder = settings.adzuna_app_key_set ? "Saved — leave blank to keep" : "Optional";
  const error = document.getElementById("smtp-error");
  error.textContent = settings.smtp_error || "";
  formReady = true;
}

function overviewShell() {
  return `
    <div id="banner-slot"></div>
    <div class="stats" id="send-stats">
      <div class="stat"><b id="sent-day">0</b><span>Sent today</span></div>
      <div class="stat"><b id="sent-week">0</b><span>Sent this week</span></div>
      <div class="stat"><b id="sent-month">0</b><span>Sent this month</span></div>
    </div>
    <div class="layout">
      <article class="card">
        <h2>Latest hiring companies</h2>
        <p class="meta" id="cycle-line"></p>
        <p class="meta" id="summary-line"></p>
        <div id="recent-companies"></div>
      </article>
      <div>
        <article class="card">
          <h2>Local and international</h2>
          <div id="mix"></div>
          <p class="note" id="adzuna-note">Add Adzuna keys on the Agent page to pull more South African adverts. Roles already located in South Africa are marked Local.</p>
        </article>
        <article class="card" style="margin-top:18px">
          <h2>Activity</h2>
          <div id="overview-feed"></div>
        </article>
      </div>
    </div>`;
}

function companiesShell() {
  return `
    <div class="toolbar">
      <input id="company-search" type="search" placeholder="Search company or role" aria-label="Search companies">
      <div class="filters">
        <button type="button" class="tiny" data-market="all">All</button>
        <button type="button" class="tiny" data-market="local">Local</button>
        <button type="button" class="tiny" data-market="international">International</button>
        <button type="button" class="tiny" data-market="contacts">Contacts</button>
      </div>
    </div>
    <div id="company-table"></div>
    <form id="add-contact" class="card add-box" hidden>
      <h2 id="add-company-name">Add a decision maker</h2>
      <p class="note">Use an address you already have. The agent writes the proposal and sends it at 16:00 SAST.</p>
      <input type="hidden" id="add-company-id">
      <div class="form-grid">
        <label class="field"><span>Name</span><input id="add-name" type="text"></label>
        <label class="field"><span>Title</span><input id="add-title" type="text" placeholder="Head of Talent"></label>
      </div>
      <label class="field"><span>Email</span><input id="add-email" type="email" required></label>
      <div class="actions"><button class="primary" type="submit">Save contact</button></div>
    </form>`;
}

function proposalsShell() {
  return `
    <div class="proposal-layout">
      <div class="card" id="proposal-list"></div>
      <div class="reader">
        <div class="reader-bar">
          <div>
            <h2>Letter</h2>
            <p class="note" id="letter-subject-line"></p>
          </div>
          <button type="button" class="tiny" id="edit-letter">Edit this letter</button>
        </div>
        <form id="letter-edit" class="letter-edit" hidden>
          <p class="note">Change the subject or the wording. A letter you save stays as you wrote it.</p>
          <label class="field"><span>Subject</span><input id="letter-subject" type="text" required></label>
          <label class="field"><span>Letter</span><textarea id="letter-body" required></textarea></label>
          <div class="actions"><button class="primary" type="submit">Save letter</button></div>
        </form>
        <iframe id="letter-frame" title="Proposal letter" scrolling="no"></iframe>
      </div>
    </div>`;
}

function inboxShell() {
  return `
    <div class="toolbar">
      <p class="note" id="inbox-note">Opening the mailbox…</p>
      <button type="button" class="tiny" id="inbox-refresh">Refresh inbox</button>
    </div>
    <div class="proposal-layout">
      <div class="card" id="inbox-list"></div>
      <div class="reader">
        <div class="reader-bar">
          <div>
            <h2>Message</h2>
            <p class="note" id="inbox-meta"></p>
          </div>
        </div>
        <pre class="mail-body" id="inbox-body"></pre>
        <form id="inbox-reply" class="letter-edit">
          <label class="field"><span>Reply from hr@talentcrafters.co.za</span><textarea id="inbox-reply-body" required placeholder="Write the reply"></textarea></label>
          <div class="actions"><button class="primary" type="submit">Send reply</button></div>
          <p class="note" id="inbox-reply-note"></p>
        </form>
      </div>
    </div>`;
}

function fillInbox() {
  const list = document.getElementById("inbox-list");
  if (!list) return;
  if (!inboxItems.length) {
    list.innerHTML = `<p class="empty">No messages in the inbox yet.</p>`;
    document.getElementById("inbox-meta").textContent = "";
    document.getElementById("inbox-body").textContent = "";
    return;
  }
  list.innerHTML = inboxItems.map((item) => {
    const opened = !item.unseen;
    return `
    <button type="button" class="proposal-open ${opened ? "read" : "unread"} ${item.uid === selectedMail ? "active" : ""}" data-mail="${esc(item.uid)}">
      <strong>${esc(item.subject)}</strong>
      <span class="mail-flag ${opened ? "opened" : "new"}">${opened ? "Opened" : "New"}</span>
      <span class="who">${esc(item.from_name || item.from_email)} · ${esc(item.from_email)}</span>
      <span class="who">${esc(fmt(item.date) || item.date)}</span>
    </button>`;
  }).join("");
}

function setInboxCount(value) {
  const count = Math.max(0, Number(value) || 0);
  if (data && data.stats) data.stats.inbox_new = count;
  const inboxCount = document.getElementById("count-inbox");
  if (!inboxCount) return;
  inboxCount.textContent = String(count);
  inboxCount.className = count ? "nav-count new" : "nav-count";
}

async function openInboxMessage(uid) {
  selectedMail = uid;
  const item = inboxItems.find((row) => String(row.uid) === String(uid));
  const wasNew = Boolean(item && item.unseen);
  if (item) item.unseen = false;
  fillInbox();
  if (wasNew && data && data.stats) setInboxCount(Number(data.stats.inbox_new || 0) - 1);
  const body = document.getElementById("inbox-body");
  const meta = document.getElementById("inbox-meta");
  body.textContent = "Opening this message…";
  try {
    const response = await fetch(`/api/inbox/${uid}`);
    const message = await response.json();
    if (!response.ok) throw new Error(message.detail || "That message could not be opened.");
    if (selectedMail !== uid) return;
    meta.textContent = `${message.from_name || message.from_email} · ${message.from_email}`;
    body.textContent = message.body || "(This message has no readable text.)";
  } catch (error) {
    if (item && wasNew) {
      item.unseen = true;
      fillInbox();
      if (data && data.stats) setInboxCount(Number(data.stats.inbox_new || 0) + 1);
    }
    body.textContent = error.message;
  }
}

async function loadInbox() {
  const note = document.getElementById("inbox-note");
  if (note) note.textContent = "Opening the mailbox…";
  try {
    const response = await fetch("/api/inbox");
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The mailbox could not be opened.");
    inboxItems = payload.messages || [];
    if (note) note.textContent = `${inboxItems.length} message${inboxItems.length === 1 ? "" : "s"} in hr@talentcrafters.co.za`;
    if (!inboxItems.some((item) => item.uid === selectedMail)) {
      selectedMail = inboxItems[0] ? inboxItems[0].uid : null;
    }
    fillInbox();
    if (selectedMail) await openInboxMessage(selectedMail);
  } catch (error) {
    if (note) note.textContent = error.message;
  }
}

function bindInbox() {
  document.getElementById("inbox-refresh").addEventListener("click", loadInbox);
  document.getElementById("inbox-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mail]");
    if (!button) return;
    openInboxMessage(button.dataset.mail);
  });
  document.getElementById("inbox-reply").addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = document.getElementById("inbox-reply-note");
    const button = event.target.querySelector("button[type=submit]");
    if (!selectedMail) return;
    button.disabled = true;
    note.textContent = "Sending the reply…";
    try {
      await postJson("/api/inbox/reply", {
        uid: selectedMail,
        body: document.getElementById("inbox-reply-body").value,
      });
      document.getElementById("inbox-reply-body").value = "";
      note.textContent = "Reply sent from hr@talentcrafters.co.za.";
    } catch (error) {
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  loadInbox();
}

function agentShell() {
  return `
    <div class="banner" id="agent-summary"></div>
    <p class="note" id="agent-window"></p>
    <div class="layout">
      <form id="settings-form" class="card">
        <h2>Mailbox</h2>
        <p class="note">Every proposal is sent from <b>hr@talentcrafters.co.za</b>. On Google Workspace or Microsoft 365, use an app password for that mailbox.</p>
        <div class="actions" style="margin-bottom:14px">
          <button type="button" class="tiny" id="preset-google">Google Workspace</button>
          <button type="button" class="tiny" id="preset-microsoft">Microsoft 365</button>
        </div>
        <div class="form-grid">
          <label class="field"><span>SMTP host</span><input name="smtp_host" type="text" required></label>
          <label class="field"><span>SMTP port</span><input name="smtp_port" type="number" required></label>
          <label class="field"><span>SMTP username</span><input name="smtp_user" type="text" required></label>
          <label class="field"><span>SMTP password</span><input name="smtp_password" type="password" autocomplete="new-password"></label>
          <label class="field"><span>IMAP host</span><input name="imap_host" type="text" required></label>
          <label class="field"><span>IMAP port</span><input name="imap_port" type="number" required></label>
        </div>
        <p class="error-text" id="smtp-error"></p>
        <label class="field"><span>Postal address</span><textarea name="postal_address" placeholder="Street, city, postal code, South Africa"></textarea></label>
        <label class="field"><span>Daily send cap</span><input name="daily_cap" type="number" min="1" max="40"></label>
        <label class="field"><span>Advantages in every proposal</span><textarea name="advantages"></textarea></label>
        <div class="form-grid">
          <label class="field"><span>Adzuna app id</span><input name="adzuna_app_id" type="text"></label>
          <label class="field"><span>Adzuna app key</span><input name="adzuna_app_key" type="password" autocomplete="new-password"></label>
          <label class="field"><span>Hunter API key</span><input name="hunter_api_key" type="password" autocomplete="new-password"></label>
          <label class="field"><span>Public base URL</span><input name="public_base_url" type="text" placeholder="https://outreach.example.com"></label>
        </div>
        <label class="check"><input name="hunter_auto_send" type="checkbox"><span>Also email named people returned by Hunter. Published careers and HR addresses are already emailed without this.</span></label>
        <label class="check"><input name="confirmed" type="checkbox"><span>Talent Crafters may email published recruiting contacts from hr@talentcrafters.co.za. Each message names us and includes an unsubscribe.</span></label>
        <div class="actions">
          <button class="primary" type="submit">Save and keep running</button>
          <button class="ghost" type="button" id="test-mail">Send a test to hr@</button>
          <button class="ghost" type="button" id="run-now">Run one cycle now</button>
        </div>
        <p class="note" id="form-note"></p>
      </form>
      <div>
        <article class="card">
          <h2>Activity</h2>
          <div id="agent-feed"></div>
        </article>
        <article class="card" style="margin-top:18px">
          <h2>Stopped addresses</h2>
          <ul class="feed" id="stop-list"></ul>
        </article>
      </div>
    </div>`;
}

function paint() {
  renderChrome();
  if (tab === "overview") {
    if (!document.getElementById("stats")) panel.innerHTML = overviewShell();
    fillOverview();
  } else if (tab === "companies") {
    if (!document.getElementById("company-search")) {
      panel.innerHTML = companiesShell();
      bindCompanies();
    }
    fillCompanies();
  } else if (tab === "proposals") {
    if (!document.getElementById("proposal-list")) {
      panel.innerHTML = proposalsShell();
      bindProposals();
    }
    fillProposals();
  } else if (tab === "inbox") {
    if (!document.getElementById("inbox-list")) {
      panel.innerHTML = inboxShell();
      bindInbox();
    }
  } else if (tab === "agent") {
    if (!document.getElementById("settings-form")) {
      panel.innerHTML = agentShell();
      formReady = false;
      bindAgent();
    }
    fillAgent();
  }
}

function setTab(next, options = {}) {
  if (!options.keep) {
    if (next === "companies") marketFilter = "all";
    if (next === "proposals") proposalFilter = "all";
  }
  tab = next;
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".side-stats button").forEach((button) => {
    const value = tab === "companies" ? marketFilter : proposalFilter;
    button.classList.toggle("active", button.dataset.stat === tab && button.dataset.value === value);
  });
  title.textContent = TITLES[tab][0];
  lede.textContent = TITLES[tab][1];
  panel.innerHTML = "";
  formReady = false;
  shownLetter = null;
  letterEditing = false;
  if (data) paint();
}

async function refresh() {
  try {
    const response = await fetch("/api/overview");
    if (!response.ok) return;
    data = await response.json();
    paint();
  } catch (_error) {
    document.getElementById("pill").textContent = "Reconnecting";
  }
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

function bindProposals() {
  const frame = document.getElementById("letter-frame");
  sizeLetter(frame);
  document.getElementById("edit-letter").addEventListener("click", () => {
    const current = data.outreach.find((item) => item.id === selectedProposal);
    if (!current) return;
    letterEditing = true;
    const editor = document.getElementById("letter-edit");
    editor.hidden = false;
    document.getElementById("letter-subject").value = current.subject;
    document.getElementById("letter-body").value = current.body_text;
    document.getElementById("letter-subject").focus();
  });
  document.getElementById("letter-edit").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await postJson(`/api/outreach/${selectedProposal}/letter`, {
        subject: document.getElementById("letter-subject").value,
        body_text: document.getElementById("letter-body").value,
      });
      letterEditing = false;
      shownLetter = null;
      await refresh();
    } catch (error) {
      button.disabled = false;
      alert(error.message);
    }
  });
}

function bindCompanies() {
  document.getElementById("company-search").addEventListener("input", fillCompanies);
  document.querySelectorAll("[data-market]").forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.market === marketFilter ? "true" : "false");
    button.addEventListener("click", () => {
      marketFilter = button.dataset.market;
      document.querySelectorAll("[data-market]").forEach((item) => {
        item.setAttribute("aria-pressed", item === button ? "true" : "false");
      });
      fillCompanies();
    });
  });
  document.getElementById("add-contact").addEventListener("submit", async (event) => {
    event.preventDefault();
    const companyId = document.getElementById("add-company-id").value;
    try {
      await postJson(`/api/companies/${companyId}/contacts`, {
        name: document.getElementById("add-name").value,
        title: document.getElementById("add-title").value,
        email: document.getElementById("add-email").value,
      });
      event.target.reset();
      event.target.hidden = true;
      await refresh();
    } catch (error) {
      alert(error.message);
    }
  });
}

function bindAgent() {
  const form = document.getElementById("settings-form");
  document.getElementById("preset-google").addEventListener("click", () => {
    form.smtp_host.value = "smtp.gmail.com";
    form.smtp_port.value = "587";
    form.imap_host.value = "imap.gmail.com";
    form.imap_port.value = "993";
    form.smtp_user.value = "hr@talentcrafters.co.za";
  });
  document.getElementById("preset-microsoft").addEventListener("click", () => {
    form.smtp_host.value = "smtp.office365.com";
    form.smtp_port.value = "587";
    form.imap_host.value = "outlook.office365.com";
    form.imap_port.value = "993";
    form.smtp_user.value = "hr@talentcrafters.co.za";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const note = document.getElementById("form-note");
    const payload = {
      smtp_host: form.smtp_host.value,
      smtp_port: form.smtp_port.value,
      smtp_user: form.smtp_user.value,
      imap_host: form.imap_host.value,
      imap_port: form.imap_port.value,
      postal_address: form.postal_address.value,
      daily_cap: form.daily_cap.value,
      advantages: form.advantages.value,
      public_base_url: form.public_base_url.value,
      confirmed: form.confirmed.checked,
      hunter_auto_send: form.hunter_auto_send.checked,
    };
    if (form.smtp_password.value) payload.smtp_password = form.smtp_password.value;
    if (form.hunter_api_key.value) payload.hunter_api_key = form.hunter_api_key.value;
    if (form.adzuna_app_id.value) payload.adzuna_app_id = form.adzuna_app_id.value;
    if (form.adzuna_app_key.value) payload.adzuna_app_key = form.adzuna_app_key.value;
    try {
      await postJson("/api/settings", payload);
      form.smtp_password.value = "";
      form.hunter_api_key.value = "";
      form.adzuna_app_id.value = "";
      form.adzuna_app_key.value = "";
      formReady = false;
      note.textContent = "Saved. The agent keeps running.";
      await refresh();
    } catch (error) {
      note.textContent = error.message;
    }
  });
  document.getElementById("test-mail").addEventListener("click", async () => {
    const note = document.getElementById("form-note");
    note.textContent = "Sending the mailbox check…";
    try {
      await postJson("/api/mailbox/test");
      note.textContent = "Mailbox check sent to hr@talentcrafters.co.za.";
    } catch (error) {
      note.textContent = error.message;
    }
  });
  document.getElementById("run-now").addEventListener("click", async () => {
    const note = document.getElementById("form-note");
    try {
      await postJson("/api/agent/run");
      note.textContent = "Cycle started.";
      await refresh();
    } catch (error) {
      note.textContent = error.message;
    }
  });
}

document.querySelector(".side-stats").addEventListener("click", (event) => {
  const stat = event.target.closest("[data-stat]");
  if (!stat) return;
  if (stat.dataset.stat === "companies") marketFilter = stat.dataset.value;
  if (stat.dataset.stat === "proposals") proposalFilter = stat.dataset.value;
  setTab(stat.dataset.stat, { keep: true });
});

panel.addEventListener("click", async (event) => {
  const send = event.target.closest("[data-send]");
  if (send) {
    send.disabled = true;
    try {
      await postJson(`/api/outreach/${send.dataset.send}/send`);
      selectedProposal = Number(send.dataset.send);
      await refresh();
    } catch (error) {
      send.disabled = false;
      alert(error.message);
    }
    return;
  }
  const proposal = event.target.closest("[data-proposal]");
  if (proposal) {
    selectedProposal = Number(proposal.dataset.proposal);
    fillProposals();
    return;
  }
  const allow = event.target.closest("[data-allow]");
  if (allow) {
    await postJson(`/api/contacts/${allow.dataset.allow}/allow`);
    await refresh();
    return;
  }
  const add = event.target.closest("[data-add]");
  if (add) {
    const form = document.getElementById("add-contact");
    form.hidden = false;
    document.getElementById("add-company-id").value = add.dataset.add;
    document.getElementById("add-company-name").textContent = `Add a contact at ${add.dataset.name}`;
    document.getElementById("add-email").focus();
  }
});

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

document.getElementById("pause").addEventListener("click", async () => {
  if (!data) return;
  await postJson("/api/settings", { paused: !data.agent.paused });
  await refresh();
});

refresh();
setInterval(refresh, 4000);
