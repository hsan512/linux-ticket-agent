"""Static checks for shell commands: syntax (bash -n, never executed) and known command names.

"Unknown" means "not shipped by any Ubuntu 24.04 package (Contents index), and not in the local
man database, PATH, or bash builtins". Tools installed only from source, pip, snap, or other
distros' repositories are therefore also reported as unknown.
"""

from __future__ import annotations

import gzip
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

WRAPPERS = {"sudo", "doas", "env", "time", "nohup", "command", "builtin", "exec", "nice", "ionice",
            "xargs", "watch", "timeout", "stdbuf", "strace", "unbuffer"}
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "function", "!", "{", "}", "[[", "]]"}
ENDERS = {"fi", "done", "esac"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
_PUNCT = set("();<>|&")


def _is_operator(tok: str) -> bool:
    return bool(tok) and set(tok) <= _PUNCT


def command_names(cmd: str) -> list[str]:
    """Names in command position (after |, &&, ;, wrappers like sudo), in order of appearance."""
    cmd = re.sub(r"\\[()]", " _escaped_paren_ ", cmd)  # find's \( \) are arguments, not subshells
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        tokens = list(lexer)
    except ValueError:  # unbalanced quotes; the syntax check reports it
        return []

    names: list[str] = []
    expect_cmd, skip_next, in_wrapper, in_for = True, False, False, False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if _is_operator(tok):
            if "<" in tok or ">" in tok:
                skip_next = True  # the redirection target is a file, not a command
            else:
                expect_cmd, in_wrapper = True, False
            continue
        if in_for:
            in_for = tok != "do"
            expect_cmd = not in_for
            continue
        if not expect_cmd:
            continue
        if tok == "for":
            in_for = True
            continue
        if tok in KEYWORDS or _ASSIGNMENT.match(tok):
            continue
        if in_wrapper and (tok.startswith("-") or re.fullmatch(r"[\d.]+[smhd]?", tok)):
            continue
        if tok in ENDERS:
            expect_cmd = False
            continue
        if tok.startswith("-"):  # an option (e.g. after find's escaped \( ), never a command name
            continue
        if tok.startswith(("$", "./", "../", "~")) or tok.endswith(".sh"):
            in_wrapper, expect_cmd = False, False
            continue  # variables and local scripts cannot be checked statically
        name = os.path.basename(tok) if tok.startswith("/") else tok
        names.append(name)
        in_wrapper = name in WRAPPERS
        expect_cmd = in_wrapper
    return names


def syntax_error(cmd: str, timeout_s: float = 5.0) -> str | None:
    """Parse with `bash -n` (no execution). Returns the error message, or None if valid."""
    proc = subprocess.run(["bash", "-n"], input=cmd, text=True, capture_output=True, timeout=timeout_s)
    if proc.returncode == 0:
        return None
    return proc.stderr.strip() or f"bash -n exited with {proc.returncode}"


@dataclass
class CommandCheck:
    """Result of check_command for one command line (used by eval for the invalid-command rate)."""

    command: str
    syntax_error: str | None
    unknown_names: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.syntax_error is None and not self.unknown_names


def defined_functions(commands: list[str]) -> set[str]:
    """Shell function names defined anywhere in `commands` (`f() {` or `function f`)."""
    return {a or b for cmd in commands for a, b in _FUNCTION_DEF.findall(cmd)}


def check_command(cmd: str, known: set[str]) -> CommandCheck:
    """`known` should include functions defined by the same answer (see defined_functions).
    UPPERCASE names are placeholders by our prompt convention (e.g. PDF_READER) and are skipped."""
    unknown = [n for n in command_names(cmd) if n not in known and not PLACEHOLDER.match(n)]
    return CommandCheck(command=cmd, syntax_error=syntax_error(cmd), unknown_names=unknown)


def check_answer(commands: list[str], known: set[str], ticket: str = "") -> list[CommandCheck]:
    """Names the ticket itself mentions (the asker's own scripts, placeholders like command1)
    and functions the answer defines are not counted as invented."""
    known_here = known | defined_functions(commands) | set(re.findall(r"[\w.+-]+", ticket))
    return [check_command(c, known_here) for c in commands]


# zsh builtins (zshbuiltins(1)) and common autoloaded functions; zsh is not installed here.
ZSH_BUILTINS = set("""
alias autoload bg bindkey break builtin bye cap cd chdir clone command comparguments compcall compctl
compdescribe compfiles compgroups compquote comptags comptry compvalues continue declare dirs disable
disown echo echotc echoti emulate enable eval exec exit export false fc fg float functions getcap getln
getopts hash history integer jobs kill let limit local log logout noglob popd print printf pushd pushln
pwd r read readonly rehash return sched set setcap setopt shift source stat suspend test times trap
true ttyctl type typeset ulimit umask unalias unfunction unhash unlimit unset unsetopt vared wait whence
where which zcompile zformat zftp zle zmodload zparseopts zprof zpty zregexparse zsocket zstyle ztcp
add-zsh-hook compinit promptinit zmv zcalc
""".split())


# Executables, plus bash-completion files, which also cover commands created by update-alternatives
# at install time (e.g. `wine`) that never appear as a file in the package.
_BIN_PATH = re.compile(r"^(?:(?:usr/)?(?:s?bin|games)|usr/share/bash-completion/completions)/([^/\s]+)\s")
_FUNCTION_DEF = re.compile(r"(?:^|[;&|{(\s])(?:function\s+([\w.:-]+)|([\w.:-]+)\s*\(\)\s*\{)")
PLACEHOLDER = re.compile(r"^[A-Z][A-Z0-9_]*$")


def commands_from_contents(path: Path) -> set[str]:
    """Executable names shipped by any Ubuntu package, from a dists/<suite>/Contents-<arch>.gz index."""
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        return {m.group(1) for line in f if (m := _BIN_PATH.match(line))}


def collect_known_commands(contents: Path | None = None, extra: set[str] | None = None) -> set[str]:
    """Command names from the Ubuntu package index (if given), the man database (sections 1, 6, 8),
    PATH, bash and zsh builtins, plus `extra` (e.g. names common in past accepted answers)."""
    known: set[str] = commands_from_contents(contents) if contents else set()
    known |= ZSH_BUILTINS | (extra or set())
    out = subprocess.run(["man", "-k", "."], text=True, capture_output=True).stdout
    for line in out.splitlines():
        m = re.match(r"^(.+?) \(([168])[^)]*\)", line)
        if m:
            known.update(n.strip() for n in m.group(1).split(","))
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if os.path.isdir(directory):
            known.update(os.listdir(directory))
    out = subprocess.run(["bash", "-c", "compgen -b; compgen -k"], text=True, capture_output=True).stdout
    known.update(out.split())
    return known
