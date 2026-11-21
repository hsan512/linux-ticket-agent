"""The agent answers once and the asker cannot reply (one POST /tickets per ticket), so
its prompts must not invite a follow-up (findings item 32)."""

import pytest

from agent import prompts


@pytest.mark.parametrize("name", ["DRAFT", "REVISE"])
def test_prompts_say_this_is_the_only_reply(name: str) -> None:
    text = getattr(prompts, name)
    assert "This is your only reply" in text
    assert "do not ask the asker anything" in text.lower()


def test_draft_says_what_to_do_instead_of_asking() -> None:
    assert "state your assumption or give the short variants" in prompts.DRAFT


def test_grader_keeps_pointers_to_a_needed_command() -> None:
    """Found by replaying dev searches: the grader dropped man pages that said "use
    findmnt(8) instead" because their main topic was another command."""
    assert "names a command, option, or file the answer likely needs" in prompts.GRADE
    assert "same symptom" in prompts.GRADE
    assert (
        "only on the same general topic are NOT relevant" in prompts.GRADE
    )  # still strict
