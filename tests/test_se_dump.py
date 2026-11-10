from pathlib import Path

from build_qa import (
    FilterConfig,
    answer_reject_reason,
    author_of,
    question_reject_reason,
)
from se_dump import html_to_text, is_watermark, iter_rows, parse_tags

CFG = FilterConfig()


def q(**overrides: str) -> dict[str, str]:
    row = {"Id": "10", "PostTypeId": "1", "AcceptedAnswerId": "11", "Score": "3"}
    return row | overrides


def test_parse_tags_both_formats() -> None:
    assert parse_tags("<bash><shell-script>") == ["bash", "shell-script"]
    assert parse_tags("|bash|shell-script|") == ["bash", "shell-script"]
    assert parse_tags(None) == []
    assert parse_tags("") == []


def test_html_to_text_keeps_code_and_breaks() -> None:
    html = (
        "<p>Run this:</p><pre><code>ls -la &amp;&amp; echo ok\n</code></pre>"
        "<ul><li>one</li><li>two</li></ul>"
    )
    text = html_to_text(html)
    assert "ls -la && echo ok" in text
    assert "Run this:" in text
    assert "- one" in text and "- two" in text
    assert "<" not in text


def test_watermark_ids() -> None:
    assert is_watermark({"Id": "1000000001"})
    assert is_watermark({"Id": "1000000010"})
    assert not is_watermark({"Id": "812345"})


def test_question_filters() -> None:
    assert question_reject_reason(q(), CFG) is None
    assert question_reject_reason(q(PostTypeId="5"), CFG) == "not_question"
    assert question_reject_reason(q(Id="1000000001"), CFG) == "watermark"
    assert question_reject_reason(q(AcceptedAnswerId=""), CFG) == "no_accepted_answer"
    assert question_reject_reason(q(ClosedDate="2020-01-01T00:00:00"), CFG) == "closed"
    assert question_reject_reason(q(Score="0"), CFG) == "low_question_score"


def test_answer_filters() -> None:
    assert answer_reject_reason({"Id": "11", "Score": "2"}, CFG) is None
    assert answer_reject_reason({"Id": "11", "Score": "0"}, CFG) == "low_answer_score"
    assert answer_reject_reason({"Id": "1000000010", "Score": "5"}, CFG) == "watermark"


def test_author_attribution() -> None:
    users = {"42": "alice"}
    assert author_of({"OwnerUserId": "42"}, users) == {
        "name": "alice",
        "profile_url": "https://unix.stackexchange.com/users/42",
    }
    assert author_of({"OwnerDisplayName": "bob"}, users)["name"] == "bob"
    assert author_of({"OwnerUserId": "-1"}, users)["profile_url"] is None


def test_iter_rows_streams_attributes(tmp_path: Path) -> None:
    xml = tmp_path / "Posts.xml"
    xml.write_text(
        '<?xml version="1.0"?><posts><row Id="1" Body="&lt;p&gt;hi&lt;/p&gt;" /><row '
        'Id="2" /></posts>'
    )
    rows = list(iter_rows(xml))
    assert [r["Id"] for r in rows] == ["1", "2"]
    assert rows[0]["Body"] == "<p>hi</p>"
