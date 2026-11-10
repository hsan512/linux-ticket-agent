"""Pydantic models shared by the baseline, the agent, and evaluation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    """One retrieved passage, labelled with a short citation id (D1, H2, P1, ...)."""

    id: str
    source: Literal["docs", "history", "package"]
    title: str
    url: str
    text: str
    score: float
    authors: list[str] = Field(
        default=[],
        description="People to credit: the question and answer authors for Stack "
        "Exchange passages; empty otherwise.",
    )
    license: str | None = Field(
        default=None, description="License of the passage, if known."
    )


class DraftAnswer(BaseModel):
    """Structured answer to a ticket. Used as the OpenAI structured-output schema."""

    answer: str = Field(description="Concise step-by-step answer in Markdown.")
    commands: list[str] = Field(
        description="Every shell command the answer proposes, exactly as it should be "
        "run, one per item. "
        "Use UPPERCASE words for placeholders (e.g. FILE), never <angle brackets>. "
        "Empty if none."
    )
    citations: list[str] = Field(
        description="IDs of the context passages (e.g. D2, H1) that support the "
        "answer. Empty if none."
    )


IssueType = Literal[
    "shell_scripting",
    "text_processing",
    "package_management",
    "networking",
    "filesystem_storage",
    "services_boot",
    "permissions_security",
    "hardware_drivers",
    "desktop_terminal",
    "other",
]


class Classification(BaseModel):
    """Output of the classify node."""

    issue_type: IssueType
    risk_level: Literal["low", "medium", "high"] = Field(
        description="How much damage a wrong fix could do: high = data loss, "
        "unbootable system, "
        "security exposure; medium = service disruption; low = read-only or easily "
        "undone."
    )
    missing_info: list[str] = Field(
        description="Facts the ticket must provide before it can be answered (e.g. "
        "distro, error "
        "message). Empty if the ticket is answerable as written."
    )


# Lines over 88 characters on purpose: this docstring is the tool description the LLM
# sees, so re-wrapping it would change the prompt the results were measured with.
# `noqa: E501` tells ruff to skip its line-length rule (E501) for this docstring.
class SearchDocs(BaseModel):
    """Search Linux/Unix documentation (man pages; GNU manuals for bash, coreutils, findutils,
    grep, sed, gawk, diffutils, gzip, wget) for how a command, option, or config file works."""  # noqa: E501

    query: str = Field(
        description="Focused search query, e.g. a command name plus the behaviour "
        "needed."
    )


# Lines over 88 characters on purpose: this docstring is the tool description the LLM
# sees, so re-wrapping it would change the prompt the results were measured with.
# `noqa: E501` tells ruff to skip its line-length rule (E501) for this docstring.
class SearchSimilar(BaseModel):
    """Search past solved Unix & Linux Stack Exchange questions (with accepted answers) similar
    to the ticket."""  # noqa: E501

    query: str = Field(
        description="A short restatement of the problem, in the asker's terms."
    )


class PassageGrade(BaseModel):
    """The grader's verdict on one passage (part of EvidenceGrade)."""

    id: str = Field(description="Passage ID, e.g. D2 or H1.")
    relevant: bool = Field(
        description="True only if the passage directly helps solve this ticket."
    )


class EvidenceGrade(BaseModel):
    """Output of the grade_evidence node."""

    grades: list[PassageGrade] = Field(description="One entry per passage.")
    sufficient: bool = Field(
        description="True if the relevant passages are enough to answer reliably."
    )
    missing: str = Field(
        description="What information is still needed to answer. Empty if sufficient."
    )


class AgentDraft(DraftAnswer):
    """Structured answer from the agent's draft node."""

    confidence: Literal["low", "medium", "high"] = Field(
        description="high = verified by the passages or certain; low = a guess that "
        "may not work."
    )


# Lines over 88 characters on purpose: this docstring is the tool description the LLM
# sees, so re-wrapping it would change the prompt the results were measured with.
# `noqa: E501` tells ruff to skip its line-length rule (E501) for this docstring.
class CheckCommand(BaseModel):
    """Look up which Ubuntu packages provide a command, in the Ubuntu 24.04 package index (not the
    asker's machine). Useful to confirm that a command exists before recommending it, or to find
    the package to install. Not found does not mean nonexistent: shell builtins (cd, export), names
    set up at install time (awk, vi, java), and software from outside Ubuntu's archive are not listed."""  # noqa: E501

    command: str = Field(
        description="A single command name only, without arguments or path: 'nc', not "
        "'nc -l 1234' "
        "or '/usr/bin/nc'."
    )
