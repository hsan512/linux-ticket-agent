"""Static checks of the demo page (frontend/): the security properties that must not
regress. Behaviour (every response rendered right, XSS payloads staying text) was
checked once in a headless DOM against a fake API; these keep the rules in place."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
HTML = (FRONTEND / "index.html").read_text()
JS = (FRONTEND / "app.js").read_text()
CSS = (FRONTEND / "style.css").read_text()


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.inline_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        if self.lasttag == "script" and data.strip():
            self.inline_script = True


def parsed() -> Tags:
    tags = Tags()
    tags.feed(HTML)
    return tags


def csp() -> dict[str, str]:
    [policy] = [
        a["content"]
        for t, a in parsed().tags
        if t == "meta" and a.get("http-equiv") == "Content-Security-Policy"
    ]
    return dict(part.strip().split(" ", 1) for part in policy.split(";"))


def test_csp_allows_only_this_sites_own_files() -> None:
    policy = csp()
    assert policy["default-src"] == "'none'"
    for directive in ("script-src", "style-src", "connect-src"):
        assert policy[directive] == "'self'", directive
    assert policy["base-uri"] == "'none'"
    assert "unsafe-inline" not in str(policy) and "unsafe-eval" not in str(policy)


def test_no_inline_code_that_the_csp_would_block() -> None:
    tags = parsed()
    assert not tags.inline_script
    assert not any(t == "style" for t, _ in tags.tags)
    assert not any(
        name.startswith("on") for _, attrs in tags.tags for name in attrs
    )  # onclick= and friends
    assert not any("style" in attrs for _, attrs in tags.tags)


def test_only_local_scripts_and_styles() -> None:
    for tag, attrs in parsed().tags:
        if tag == "script":
            assert attrs.get("src") == "app.js"
        if tag == "link" and attrs.get("rel") == "stylesheet":
            assert attrs.get("href") == "style.css"


@pytest.mark.parametrize(
    "pattern",
    [
        r"innerHTML",
        r"outerHTML",
        r"insertAdjacentHTML",
        r"document\.write",
        r"\beval\(",
        r"new Function",
        r"setTimeout\(\s*['\"`]",
    ],
)
def test_no_api_that_turns_text_into_code(pattern: str) -> None:
    code = "\n".join(
        line for line in JS.splitlines() if not line.strip().startswith("//")
    )
    assert not re.search(pattern, code), pattern


def test_links_only_http_and_no_tabnabbing() -> None:
    assert 'parsed.protocol === "https:" || parsed.protocol === "http:"' in JS
    assert 'target: "_blank", rel: "noopener noreferrer"' in JS
    for tag, attrs in parsed().tags:
        if tag == "a" and attrs.get("href", "").startswith("http"):
            assert "noopener" in (attrs.get("rel") or ""), attrs


def test_calls_the_api_on_the_same_origin() -> None:
    assert re.search(r'fetch\("/tickets"', JS)
    assert not re.search(r"fetch\(\s*['\"]https?://", JS)


@pytest.mark.parametrize("status", [422, 429, 503, 500, 504])
def test_every_error_status_has_a_message(status: int) -> None:
    assert f"case {status}:" in JS


def test_only_plain_text_error_bodies_are_shown() -> None:
    assert 'if (!type.includes("text/plain")) return null;' in JS


def test_waits_less_than_forever() -> None:
    assert re.search(r"TIMEOUT_MS = 70000", JS) and "AbortController" in JS


def test_privacy_notice_and_attribution() -> None:
    assert "Do not include passwords, keys, or personal data" in HTML
    assert "sent to OpenAI" in HTML and "stored for review" in HTML
    assert "CC BY-SA" in HTML and "unix.stackexchange.com" in HTML
    assert "AI-generated" in HTML


def test_answer_text_is_never_auto_linked() -> None:
    """Only citations (from the retrieved corpus) become links; a URL the model writes
    in its answer, e.g. after a prompt injection, stays plain text."""
    assert 'el("a"' in JS
    links = re.findall(r'el\("a", \{[^}]*\}', JS)
    assert all("cite" in link or "href: url" in link for link in links), links
