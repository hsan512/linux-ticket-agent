"""Checks on the shell commands of a draft answer before it is shown: syntax, command names, and
danger. Pure functions with no state or LLM; the validate node decides what each issue causes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agent.tools import danger_level as dl
from agent.tools.command_check import PLACEHOLDER, ZSH_BUILTINS, defined_functions, syntax_error
from agent.tools.packages import CommandPackageLookup

# bash 5.2 builtins and keywords (`compgen -b; compgen -k`), frozen here so the check gives the same
# result on every machine (no Ubuntu package ships them as files)
BASH_BUILTINS = frozenset("""
. : [ alias bg bind break builtin caller cd command compgen complete compopt continue declare dirs
disown echo enable eval exec exit export false fc fg getopts hash help history jobs kill let local
logout mapfile popd printf pushd pwd read readarray readonly return set shift shopt source suspend
test times trap true type typeset ulimit umask unalias unset wait
if then else elif fi case esac for select while until do done in function time { } ! [[ ]] coproc
""".split())

# Names the Debian alternatives system creates at install time, so no package lists them as files
ALTERNATIVES = frozenset("""
awk nawk vi view ex editor pager java javac jar cc c++ iptables ip6tables iptables-save
iptables-restore ip6tables-save ip6tables-restore arptables ebtables netcat rsh rlogin rcp telnet
ftp x-terminal-emulator x-www-browser www-browser lzma unlzma mt aptitude
""".split())

COMMAND_NAME = re.compile(r"[A-Za-z0-9_][\w.+-]*")


@dataclass(frozen=True)
class Issue:
    """One problem in a draft's commands. The validate node routes on `kind`; the reviser and
    humans read `message`."""

    command: str  # the command line exactly as in the draft's `commands`
    kind: Literal["syntax", "unknown_command", "catastrophic", "destructive"]
    message: str  # self-contained; shown to the reviser and to humans


def syntax_issue(line: str) -> Issue | None:
    """`bash -n` on the line: parsed, never executed."""
    error = syntax_error(line)
    return Issue(line, "syntax", f"bash -n: {error}") if error else None


def syntax_issues(commands: list[str]) -> list[Issue]:
    """Syntax issues for a draft's command items. Models sometimes split one multi-line script
    over several items ("while read x; do", "...", "done"): each fragment fails `bash -n` alone,
    so if the items joined are valid as a script, no item is reported."""
    if commands and syntax_error("\n".join(commands)) is None:
        return []
    return [issue for line in commands if (issue := syntax_issue(line))]


def context_names(commands: list[str], ticket: str) -> set[str]:
    """Names that exist in this conversation although no Ubuntu package ships them: every word of
    the ticket (the asker's own scripts and tools; over-inclusion is harmless because the set only
    exempts) and functions the answer itself defines. Compute once per answer, not per line."""
    return set(re.findall(r"[\w.+-]+", ticket)) | defined_functions(commands)


def needs_lookup(name: str, context: set[str]) -> bool:
    """False for what the package table cannot answer: anything not shaped like a command name
    (paths, $variables, parser noise such as `*` or `@reboot`), bash/zsh builtins, alternatives,
    UPPERCASE placeholders (the draft prompt's convention, e.g. PDF_READER), and context names."""
    return bool(COMMAND_NAME.fullmatch(name)) and not (
        name in BASH_BUILTINS or name in ZSH_BUILTINS or name in ALTERNATIVES
        or PLACEHOLDER.match(name) or name in context
    )


def command_names(line: str) -> set[str]:
    """Names of the commands a line runs, from the same parser as the danger check: the command as
    written (sudo, nohup, ...) and what it runs through wrappers and sh -c (apt, rm, ...).
    Empty when the line cannot be split at all; syntax_issue reports that case."""
    try:
        parsed = dl.commands(line)
    except ValueError:
        return set()
    names = set()
    for c in parsed:
        names.add(c.name)
        names.update(u.name for u in dl.unwrap(c))
    names.discard("")  # a bare redirect such as `> file` has no command
    return names


def validate_commands(commands: list[str], lookup: CommandPackageLookup, ticket: str = "") -> list[Issue]:
    """All issues in a draft's commands, line by line: syntax, unknown command names, danger.
    `ticket` is reference only: names the asker mentions count as existing on their machine."""
    issues = []
    context = context_names(commands, ticket)
    syntax = {issue.command: issue for issue in syntax_issues(commands)}  # judged over all items at once
    for command in commands:
        if command in syntax:
            issues.append(syntax[command])
        cmd_names = command_names(command)
        for cmd_name in cmd_names:
            package = lookup.packages(cmd_name)
            if not package:
                needs_lk = needs_lookup(cmd_name, context)
                if needs_lk:
                    package_issue = Issue(command, "unknown_command", f"{cmd_name}: not supported by any known Ubuntu package")
                    issues.append(package_issue)
        dangers = dl.find_dangers(command)
        if dangers:
            for dgr in dangers:
                danger_issue = Issue(command, dgr.level, f"{dgr.command}: {dgr.reason}")
                issues.append(danger_issue)
    return issues


def format_issues(issues: list[Issue]) -> str:
    """Issues as the reviser sees them, one "- `command` (kind): message" line each.
    Formatting only: the caller chooses which issues to show."""
    return "\n".join(f"- `{issue.command}` ({issue.kind}): {issue.message}" for issue in issues)
