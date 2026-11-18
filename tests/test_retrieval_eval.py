"""Tests for the retrieval-only score (eval/retrieval_eval.py): which answer terms are
extracted, and when a term counts as found. No Qdrant or API calls."""

import pytest

from eval.retrieval_eval import answer_terms, code_spans, found, score, span_terms


def ticket(answer_html: str, question: str = "How?", title: str = "t") -> dict:
    return {"title": title, "question_text": question, "answer_html": answer_html}


def test_code_spans_decode_html() -> None:
    assert code_spans("<p>x</p><pre><code>a &amp;&amp; b</code></pre>") == ["a && b"]


@pytest.mark.parametrize(
    ("span", "expected"),
    [
        ("ss -ltnp", {"ss", "-ltnp"}),
        ("sudo findmnt --output SOURCE", {"findmnt", "--output"}),
        ("sgdisk --delete=3 /dev/sda", {"sgdisk", "--delete", "/dev/sda"}),
        ("cat /etc/fstab | awk '{print $2}'", {"cat", "/etc/fstab", "awk"}),
        (
            "sysctl net.bridge.bridge-nf-call-iptables=1",
            {"sysctl", "net.bridge.bridge-nf-call-iptables"},
        ),
        ("mapfile", {"mapfile"}),  # inline code: a single command
        ('echo "$x" > out', {"echo"}),  # quoted variables and plain words dropped
        ("cd ~/.config/", {"cd", "~/.config"}),
    ],
)
def test_span_terms(span: str, expected: set[str]) -> None:
    assert span_terms(span) == expected


def test_answer_terms_drop_common_words_and_what_the_question_says() -> None:
    t = ticket(
        "<code>sudo ss -ltnp</code> <code>grep 8080</code> <code>lsof -i</code>",
        question="I ran lsof but it shows nothing",
    )
    # sudo, grep: stop list; 8080: a number; lsof: already in the question
    assert answer_terms(t) == ["-ltnp", "ss"]


def test_answer_terms_drop_short_tokens() -> None:
    assert answer_terms(ticket("<code>ls -l</code>")) == []


@pytest.mark.parametrize(
    ("term", "text", "hit"),
    [
        ("-l", "use ls -ltnp", False),  # an option is not a prefix of another
        ("-ltnp", "ss -ltnp here", True),
        ("ss", "the class keyword", False),
        ("ss", "run ss now", True),
        ("tun-mtu", "set --tun-mtu 1400", True),  # config name vs. man page option
        ("--tun-mtu", "set tun-mtu 1400", False),
        ("/etc/fstab", "edit /etc/fstab.", True),
        ("/etc/fs", "edit /etc/fstab", False),
        ("Mapfile", "mapfile -t", False),  # case-sensitive
    ],
)
def test_found(term: str, text: str, hit: bool) -> None:
    assert found(term, text) is hit


def test_score_per_k() -> None:
    result = score(["ss", "-ltnp"], ["nothing", "ss only", "ss -ltnp"], ks=(1, 2, 3))
    assert result[1] == {"recall": 0.0, "hit": 0, "found": []}
    assert result[2]["recall"] == 0.5 and result[2]["hit"] == 1
    assert result[3]["recall"] == 1.0
