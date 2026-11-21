// Behaviour of index.html. Builds every element with createElement + textContent,
// never innerHTML, so server text cannot become markup.
"use strict";

// The API's rule: longer questions go to a human without running the agent
// (MAX_QUESTION_CHARS in api/main.py). Shown as a hint, not enforced here.
const AGENT_LIMIT = 4000;
// CloudFront gives up on the origin after 60 s; stop waiting a little later.
const TIMEOUT_MS = 70000;
// Citation IDs as the agent writes them: D1 (docs), H2 (similar questions), ...
const CITE_ID = /^[A-Z]{1,3}\d{1,3}$/;

const EXAMPLE = {
  title: "How do I see which process is listening on port 8080?",
  question_text:
    "Starting my web server fails with \"address already in use\" on port 8080. " +
    "How can I find out which process is using the port, so I can stop it?",
};

const form = document.getElementById("ticket-form");
const titleInput = document.getElementById("title");
const questionInput = document.getElementById("question");
const hint = document.getElementById("question-hint");
const submitButton = document.getElementById("submit");
const result = document.getElementById("result");

// --- small DOM helpers: text only, never HTML ---------------------------------------

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "className") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

// Only http(s) links: a javascript: URL in a citation must not become clickable.
function safeUrl(url) {
  try {
    const parsed = new URL(url, location.href);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

// --- the answer: a small, safe subset of Markdown ------------------------------------
// The agent writes `code`, **bold**, lists, ``` fenced blocks ```, and citation tags
// like [D1]. Anything else is shown as plain text.

function renderInline(text, citationIds) {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[A-Z]{1,3}\d{1,3}\])/);
  return parts.filter(Boolean).map((part) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return el("code", { text: part.slice(1, -1) });
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return el("strong", { text: part.slice(2, -2) });
    }
    const id = part.slice(1, -1);
    if (part.startsWith("[") && CITE_ID.test(id) && citationIds.has(id)) {
      return el("a", { className: "cite", href: `#cite-${id}`, text: `[${id}]` });
    }
    return document.createTextNode(part);
  });
}

function renderList(lines, citationIds) {
  const ordered = /^\d+\.\s/.test(lines[0]);
  return el(ordered ? "ol" : "ul", {}, lines.map((line) =>
    el("li", {}, renderInline(line.replace(/^(\d+\.|[-*])\s+/, ""), citationIds))));
}

const isListLine = (line) => /^(\d+\.|[-*])\s+/.test(line);

function renderBlock(block, citationIds) {
  const lines = block.split("\n");
  // the warnings finalize adds before the answer: a bold heading, then "- `cmd` ..."
  const callout = /^\*\*(Warning|Note):/.exec(lines[0]);
  if (callout) {
    const box = el("div", { className: `callout ${callout[1].toLowerCase()}` });
    box.append(el("p", {}, renderInline(lines[0], citationIds)));
    if (lines.length > 1) box.append(renderList(lines.slice(1), citationIds));
    return box;
  }
  if (lines.every(isListLine)) return renderList(lines, citationIds);
  // a paragraph, possibly followed by its list ("For example:\n- a\n- b")
  const firstList = lines.findIndex(isListLine);
  if (firstList > 0 && lines.slice(firstList).every(isListLine)) {
    const fragment = document.createDocumentFragment();
    fragment.append(el("p", {}, renderInline(lines.slice(0, firstList).join("\n"), citationIds)));
    fragment.append(renderList(lines.slice(firstList), citationIds));
    return fragment;
  }
  return el("p", {}, renderInline(block, citationIds));
}

function renderAnswer(answer, citationIds) {
  const container = el("div", { className: "answer" });
  // fenced code first, so blank lines inside a code block do not split it
  const chunks = answer.split(/^```[^\n]*\n([\s\S]*?)^```[ \t]*$/m);
  chunks.forEach((chunk, i) => {
    if (i % 2 === 1) {
      container.append(el("pre", {}, [el("code", { text: chunk.replace(/\n$/, "") })]));
      return;
    }
    for (const block of chunk.split(/\n\s*\n/)) {
      if (block.trim()) container.append(renderBlock(block.trim(), citationIds));
    }
  });
  return container;
}

function renderCitations(citations) {
  const list = el("ol", { className: "citations" });
  for (const c of citations) {
    const url = safeUrl(c.url);
    const title = url
      ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer", text: c.title })
      : el("span", { text: c.title });
    const meta = [];
    if (c.authors && c.authors.length) meta.push(`by ${c.authors.join(", ")}`);
    if (c.license) meta.push(c.license);
    const item = el("li", { id: `cite-${c.id}` }, [el("span", { className: "cite-id", text: c.id }), title]);
    if (meta.length) item.append(el("div", { className: "meta", text: meta.join(" · ") }));
    list.append(item);
  }
  return list;
}

// --- results -------------------------------------------------------------------------

function show(...nodes) {
  result.replaceChildren(...nodes);
  result.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showMessage(text, kind = "error") {
  show(el("p", { className: `status ${kind}`, text }));
}

function showTicket(ticket) {
  if (ticket.status === "answered") {
    const citations = ticket.citations || [];
    const ids = new Set(citations.map((c) => c.id));
    const nodes = [
      el("p", { className: "status answered", text: "Answered" }),
      renderAnswer(ticket.answer || "", ids),
    ];
    if (citations.length) nodes.push(el("h2", { text: "Sources" }), renderCitations(citations));
    show(...nodes);
    return;
  }
  show(
    el("p", { className: "status", text: "Handed to a human" }),
    el("p", { text: ticket.escalation_reason || "The agent did not answer this one itself." }),
    el("p", { className: "meta" }, [
      "Reference: ", el("span", { className: "ref", text: ticket.ticket_id }),
    ]),
  );
}

// Error bodies: FastAPI sends JSON {"detail": ...}; the CloudFront edge sends plain
// text (the "demo is offline" 503 and its 404). Tell them apart by Content-Type.
async function errorText(response) {
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const body = await response.json().catch(() => ({}));
    return typeof body.detail === "string" ? body.detail : null;
  }
  if (!type.includes("text/plain")) return null;  // e.g. an HTML 502 page: not shown
  const text = (await response.text().catch(() => "")).trim();
  return text && text.length < 500 ? text : null;
}

async function showError(response) {
  const detail = await errorText(response);
  switch (response.status) {
    case 422:
      return showMessage("The title or question is missing or too long.");
    case 429:
      return showMessage("Rate limit reached: 5 questions per minute, 30 per day. Please try again later.");
    case 503:  // the daily budget (JSON) or the demo being offline (text)
      return showMessage(detail || "The service is unavailable right now. Please try again later.");
    case 500:  // detail carries the reference to quote
      return showMessage(detail || "Internal error.");
    case 504:
      return showMessage("The agent took too long to answer. Please try again.");
    default:
      return showMessage(detail || `Unexpected error (HTTP ${response.status}).`);
  }
}

// --- the form --------------------------------------------------------------------------

function updateHint() {
  const count = questionInput.value.length;
  const over = count > AGENT_LIMIT;
  hint.textContent = `${count.toLocaleString()} / ${AGENT_LIMIT.toLocaleString()} characters` +
    (over ? " — longer questions go to a human" : "");
  hint.classList.toggle("over", over);
}

questionInput.addEventListener("input", updateHint);

document.getElementById("example").addEventListener("click", () => {
  titleInput.value = EXAMPLE.title;
  questionInput.value = EXAMPLE.question_text;
  updateHint();
  titleInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = titleInput.value.trim();
  const question = questionInput.value.trim();
  if (!title || !question) {
    showMessage("Please fill in both the title and the question.");
    (title ? questionInput : titleInput).focus();
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = "Working…";
  showMessage("Working on it… this usually takes 10–30 seconds.", "pending");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch("/tickets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, question_text: question }),
      signal: controller.signal,
    });
    if (response.ok) showTicket(await response.json());
    else await showError(response);
  } catch (error) {
    showMessage(error.name === "AbortError"
      ? "The agent took too long to answer. Please try again."
      : "The request failed. Check your connection and try again.");
  } finally {
    clearTimeout(timer);
    submitButton.disabled = false;
    submitButton.textContent = "Ask the agent";
  }
});
