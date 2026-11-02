"""Static danger check for shell commands proposed by the agent (never executed).

Words that bash expands (variables, command substitution) are judged by their worst case: what
the command would do if the expansion came out empty or unexpected.

This is a guardrail against likely mistakes in model-written commands, not a security boundary:
deliberate obfuscation (eval, variables holding commands, encoded payloads) is not detected.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Literal

import bashlex
import bashlex.ast
import bashlex.errors

Level = Literal["catastrophic", "destructive"]
SEVERITY: dict[Level, int] = {"destructive": 1, "catastrophic": 2}  # higher = worse
SYSTEM_DIRS = frozenset({"/", "/bin", "/boot", "/etc", "/home", "/lib", "/lib64", "/opt",
                         "/root", "/sbin", "/usr", "/var"})


# --- parsing: what bash would run (no judgement here) ---

@dataclass(frozen=True)
class Word:
    """One shell word after quote removal. Expansions are not performed, so `text` alone cannot
    tell `$HOME` (a variable) from `'$HOME'` (a literal name); `expansions` can."""

    text: str
    # kinds of expansion bash performs on this word: "parameter" ($X, ${X}), "tilde" (~),
    # "commandsubstitution" ($(...), `...`), "processsubstitution" (<(...)); empty = literal
    expansions: frozenset[str] = frozenset()
    # the word if every variable and substitution came out empty (see _worst_case);
    # derived from the other fields, so not compared; defaults to `text`
    worst_case: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.worst_case is None:
            object.__setattr__(self, "worst_case", self.text)  # frozen: set once, here


@dataclass(frozen=True)
class SimpleCommand:
    """One command as bash would run it: its words, plus its redirections."""

    words: tuple[Word, ...]  # command name first, then arguments
    redirects: tuple[tuple[str, Word], ...] = ()  # (operator, target), e.g. (">", Word("/dev/sda"))
    stdin_from: str = field(default="", compare=False)  # name of the command piped into this one

    @property
    def name(self) -> str:
        return self.words[0].text if self.words else ""

    @property
    def options(self) -> tuple[str, ...]:
        """Arguments starting with "-" before a "--" (syntax only: `-7` in `find -mtime -7` is a value)."""
        return tuple(w.text for w in self._split()[0])

    @property
    def operands(self) -> tuple[Word, ...]:
        """Every other argument. Includes option values (the `x` in `-name x`) and things like
        `of=/dev/sda` or `777`; each rule decides which operands are targets."""
        return self._split()[1]

    @property
    def text(self) -> str:
        """Approximate source text (quotes removed), for messages."""
        return " ".join([w.text for w in self.words] + [f"{op} {t.text}" for op, t in self.redirects])

    def _split(self) -> tuple[list[Word], tuple[Word, ...]]:
        """(options, operands): words after `--` are always operands."""
        args = self.words[1:]
        for i, w in enumerate(args):
            if w.text == "--":
                return [a for a in args[:i] if a.text.startswith("-")], (
                    tuple(a for a in args[:i] if not a.text.startswith("-")) + args[i + 1:])
        return [a for a in args if a.text.startswith("-")], tuple(a for a in args if not a.text.startswith("-"))


def _parameter_worst_case(value: str) -> str | None:
    """What ${...} becomes in the worst case, from bashlex's parameter value (e.g. "DIR",
    "DIR:?", "DIR:-/tmp"). None means bash would stop rather than expand to "" (":?")."""
    if ":?" in value:
        return None
    m = re.match(r"\w+(:?)[-=](.*)", value, re.DOTALL)  # ${X:-default}, ${X:=default}, ${X-default}
    if m and m.group(1):  # with ":", the default also replaces an empty value
        return m.group(2)
    return ""  # $X, ${X}, ${X?} (stops only if unset), ${X-default} (set but empty stays empty)


def _worst_case(node: bashlex.ast.node, source: str) -> str:
    """The word with every substitution and variable replaced by what it gives when empty,
    then quotes removed, e.g. `"$DIR"/*` -> `/*`, `/usr/$(x)` -> `/usr/`. Tildes are kept."""
    start = node.pos[0]
    text = source[start:node.pos[1]]  # still quoted, so positions of the parts line up
    for p in sorted(getattr(node, "parts", []), key=lambda p: p.pos[0], reverse=True):
        if p.kind in ("commandsubstitution", "processsubstitution"):
            replacement = ""
        elif p.kind == "parameter":
            replacement = _parameter_worst_case(p.value)
            if replacement is None:
                continue
        else:
            continue
        text = text[:p.pos[0] - start] + replacement + text[p.pos[1] - start:]
    try:  # let bash's own rules remove the quotes that are left
        return " ".join(w.word for w in bashlex.parse(f": {text}")[0].parts[1:])
    except bashlex.errors.ParsingError:
        return node.word


def _word(node: bashlex.ast.node, source: str) -> Word:
    return Word(node.word, frozenset(p.kind for p in getattr(node, "parts", [])), _worst_case(node, source))


def _children(node: bashlex.ast.node) -> list[bashlex.ast.node]:
    """Direct child nodes. bashlex stores them under different attributes depending on the node kind:
    `parts` (lists, pipelines, commands, words), `list` (compound commands such as { ...; } and if),
    `command` (inside $(...), `...`, <(...)), and `output` (a redirect's target word)."""
    kids = list(getattr(node, "parts", [])) + list(getattr(node, "list", []))
    for attr in ("command", "output"):
        child = getattr(node, attr, None)
        if isinstance(child, bashlex.ast.node):  # a redirect like 2>&1 has an int here, not a node
            kids.append(child)
    return kids


def find_commands(node: bashlex.ast.node, source: str) -> list[SimpleCommand]:
    """Every command bash would run for this parse tree, inner first: a command nested in
    command or process substitution ($(...), `...`, <(...)) comes before the command containing
    it, as bash runs it first. Also includes if/for/while bodies and function bodies (counted even
    though they only run when called).

    A substitution inside single quotes is plain text, and bashlex parses it as a plain word,
    so it is not returned.
    """
    found: list[SimpleCommand] = []
    if node.kind == "pipeline":  # record what each command reads from, e.g. curl ... | sh
        previous = ""
        for part in node.parts:
            if part.kind == "pipe":
                continue
            cmds = find_commands(part, source)
            if previous and cmds and part.kind == "command":
                cmds[-1] = replace(cmds[-1], stdin_from=previous)  # inner first: the part itself is last
            found.extend(cmds)
            previous = cmds[-1].name if cmds else ""
        return found
    for child in _children(node):  # substitutions sit inside a command's words and redirects
        found.extend(find_commands(child, source))
    if node.kind == "command":
        words = tuple(_word(p, source) for p in node.parts if p.kind == "word")
        redirects = tuple((p.type, _word(p.output, source)) for p in node.parts
                          if p.kind == "redirect" and isinstance(p.output, bashlex.ast.node))
        if words or redirects:  # a bare redirect such as `> /dev/sda` still writes to the target
            found.append(SimpleCommand(words, redirects))
    return found


def parse_commands(cmd: str) -> list[SimpleCommand]:
    """All commands in a command line, inner first. Raises bashlex.errors.ParsingError for invalid
    syntax and for some valid syntax bashlex rejects ([[ ]], arrays), and NotImplementedError for
    constructs it does not implement (time, case, $((...))); see approximate_commands."""
    return [c for tree in bashlex.parse(cmd) for c in find_commands(tree, cmd)]



KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!",
                      "time", "function", "esac"})
SKIPPED = frozenset({"for", "select", "case", "[[", "[", "test"})  # segments that run no command of interest
_SUBSTITUTION = re.compile(r"\$\(\(.*?\)\)|\$\([^()]*\)|`[^`]*`")  # innermost first; $((...)) is arithmetic
_PARAMETER = re.compile(r"\$\{([^}]*)\}|\$(\w+)")
_PLACEHOLDER = "__substitution__"


def _approximate_word(text: str) -> Word:
    """A Word from a shlex token: expansions guessed from its text (quotes are already gone, so
    '$HOME' in single quotes looks like a variable: a conservative mistake)."""
    expansions = set()
    if _PLACEHOLDER in text:
        expansions.add("commandsubstitution")
    if _PARAMETER.search(text):
        expansions.add("parameter")
    if text.startswith("~"):
        expansions.add("tilde")

    def worst(m: re.Match[str]) -> str:
        value = _parameter_worst_case(m.group(1) or m.group(2))
        return m.group(0) if value is None else value

    worst_case = _PARAMETER.sub(worst, text.replace(_PLACEHOLDER, ""))
    return Word(text.replace(_PLACEHOLDER, "$(...)"), frozenset(expansions), worst_case)


def approximate_commands(cmd: str) -> list[SimpleCommand]:
    """Fallback when bashlex cannot parse `cmd`: split with shlex on operators, drop keywords and
    test expressions, guess expansions from the text. Raises ValueError for unbalanced quotes."""
    inner: list[SimpleCommand] = []

    def pull_out(m: re.Match[str]) -> str:
        sub = m.group(0)
        if sub.startswith("$(("):
            return "0"
        inner.extend(approximate_commands(sub[2:-1] if sub.startswith("$(") else sub[1:-1]))
        return _PLACEHOLDER

    previous = None
    while previous != cmd:  # repeat so nested substitutions are taken out from the inside
        previous, cmd = cmd, _SUBSTITUTION.sub(pull_out, cmd)
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    tokens = list(lexer)

    found: list[SimpleCommand] = []
    words: list[str] = []
    redirects: list[tuple[str, Word]] = []
    stdin_from = ""

    def flush(pipe: bool) -> None:
        nonlocal stdin_from
        while words and (words[0] in KEYWORDS or re.fullmatch(r"\w+=.*", words[0])):
            words.pop(0)  # leading keywords and VAR=value assignments
        if (words or redirects) and not (words and words[0] in SKIPPED):
            found.append(SimpleCommand(tuple(map(_approximate_word, words)), tuple(redirects), stdin_from))
        stdin_from = (words[0] if words else "") if pipe else ""
        words.clear()
        redirects.clear()

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t and set(t) <= set("();<>|&"):
            if "<" in t or ">" in t:
                if i + 1 < len(tokens) and t in (">", ">>", ">|", "&>"):
                    redirects.append((t, _approximate_word(tokens[i + 1])))
                i += 2  # the next token is a file or a descriptor, not a command
                continue
            flush(pipe=t in ("|", "|&"))
        else:
            words.append(t)
        i += 1
    flush(pipe=False)
    return inner + found


# --- judging: rules per command ---

@dataclass(frozen=True)
class Finding:
    """One rule that fired on one simple command."""

    level: Level
    command: str  # SimpleCommand.text of the offending command
    reason: str  # shown to the reviser and in traces, e.g. "recursive delete of /"


Rule = Callable[[SimpleCommand], tuple[Level, str] | None]  # (level, reason) or None if harmless


def _normalise(path: str) -> str:
    """"/usr/*" -> "/usr", "//*" -> "/", "/tmp/../usr" -> "/usr"; "/" stays "/"."""
    if not path or path == "/":
        return path
    if path.endswith("/*"):
        path = path[:-1]  # "/usr/*" -> "/usr/"; normpath then drops the trailing "/"
    path = posixpath.normpath(path)
    path = path.replace ("//", "/")
    return path

HOME = re.compile(r"(~|\$HOME|\$\{HOME(:?[?\-=][^}]*)?\})(/\*?)?")  # the home directory itself
# Whole disks and partitions; uppercase letters cover placeholders such as /dev/sdX.
DISK = re.compile(r"/dev/(sd[a-zA-Z]|hd[a-zA-Z]|vd[a-zA-Z]|xvd[a-zA-Z]|nvme\d|mmcblk\d|md\d|mapper/|disk/)")
RUNTIME = frozenset({"parameter", "commandsubstitution"})  # expansions whose value is unknown statically


def _broad_target(operand: Word) -> str | None:
    """Why a recursive operation on `operand` is catastrophic (/, a system directory, or home,
    also after an empty expansion), or None if it is not."""
    text, worst = _normalise(operand.text), _normalise(operand.worst_case)
    if operand.expansions & {"tilde", "parameter"} and HOME.fullmatch(text):
        return f"{operand.text} is the whole home directory"
    if worst in SYSTEM_DIRS:
        what = "/ (the whole system)" if worst == "/" else f"the system directory {worst}"
        if worst == text:
            return f"{operand.text} is {what}"
        return f"{operand.text} becomes {what} if the variable or command output is empty"
    return None

def _rm(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """rm: catastrophic if recursive on /, a system directory, or home (including what a target
    becomes when an expansion is empty); otherwise destructive; None with nothing to delete."""
    if "--no-preserve-root" in cmd.options:
        return "catastrophic", "--no-preserve-root turns off rm's refusal to delete /"
    recursive = any(o == "--recursive" or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", o) for o in cmd.options)
    if recursive:
        for operand in cmd.operands:
            why = _broad_target(operand)
            if why:
                return "catastrophic", f"recursive delete: {why}"
    if not cmd.operands:
        return None  # nothing to delete (rm fed by xargs is handled with wrappers)
    targets = " ".join(o.text for o in cmd.operands)
    return "destructive", f"{'recursive ' if recursive else ''}delete of {targets}"


def _device(word: Word, value: str) -> str | None:
    """Why writing raw data to `value` (the text of `word`, or a part of it) is catastrophic."""
    if DISK.match(value):
        return f"{value} is a disk or partition: everything on it is lost"
    if word.expansions & RUNTIME:
        return f"{value} is only known at run time and may be a disk"
    return None


def _dd(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """dd copies bytes from if= to of=. Catastrophic when of= is a disk (or unknown); destructive
    when it overwrites a regular file; None without of= (output goes to stdout)."""
    for operand in cmd.operands:
        if operand.text.startswith("of="):
            target = operand.text[3:]
            why = _device(operand, target)
            if why:
                return "catastrophic", f"dd writes over {target}: {why}"
            if target.startswith("/dev/"):  # /dev/null, /dev/stdout: not storage
                return None
            return "destructive", f"dd overwrites the file {target}"
    return None


def _format(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """mkfs*, mkswap, wipefs: create a new filesystem (or erase its signature), losing all data.
    Catastrophic on a disk or partition (or an unknown target); destructive on an image file."""
    for operand in cmd.operands:
        why = _device(operand, operand.text)
        if why:
            return "catastrophic", f"{cmd.name} erases {operand.text}: {why}"
    if cmd.operands:
        return "destructive", f"{cmd.name} erases {' '.join(o.text for o in cmd.operands)}"
    return None


def _redirect(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """Any command whose output is redirected onto a disk, e.g. cat image.iso > /dev/sda."""
    for op, target in cmd.redirects:
        if op in (">", ">>", ">|", "&>"):
            why = _device(target, target.text)
            if why:
                return "catastrophic", f"output redirected onto {target.text}: {why}"
    return None


def _recursive(cmd: SimpleCommand) -> bool:
    """-r/-R alone or bundled (`-Rf`), or --recursive."""
    return any(o == "--recursive" or re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", o) for o in cmd.options)


def _chmod_chown(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """chmod/chown -R on /, a system directory, or home: catastrophic (breaks sudo, ssh, boot).
    chmod -R making everything world-writable (777, 666, a+w, o+w): destructive (a security hole).
    The first operand is the mode or owner; the rest are targets."""
    if not _recursive(cmd) or len(cmd.operands) < 2:
        return None
    setting, targets = cmd.operands[0].text, cmd.operands[1:]
    for target in targets:
        why = _broad_target(target)
        if why:
            return "catastrophic", f"recursive {cmd.name} {setting}: {why}"
    if cmd.name == "chmod" and re.fullmatch(r"0?[0-7]?[67][67]|[ao]*\+[rwx]*w[rwx]*", setting) and \
            (setting.endswith(("66", "77")) or setting[0] in "ao"):
        return "destructive", f"chmod -R {setting} makes {' '.join(t.text for t in targets)} writable by every user"
    return None


def _find(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """find ... -delete or -exec rm: catastrophic when it searches from /, a system directory, or
    home (matches can be anywhere); otherwise destructive."""
    args = [w.text for w in cmd.words[1:]]
    deletes = "-delete" in args or any(
        a in ("-exec", "-execdir", "-ok", "-okdir") and i + 1 < len(args) and args[i + 1] == "rm"
        for i, a in enumerate(args))
    if not deletes:
        return None
    roots = []
    for w in cmd.words[1:]:  # starting points come before the first expression
        if w.text.startswith("-") or w.text in ("(", "!"):
            break
        roots.append(w)
    for root in roots:
        why = _broad_target(root)
        if why:
            return "catastrophic", f"find deletes matches under {root.text}: {why}"
    where = " ".join(r.text for r in roots) or "."
    return "destructive", f"find deletes the files it matches under {where}"


SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
DOWNLOADERS = frozenset({"curl", "wget", "fetch"})


def _shell(cmd: SimpleCommand) -> tuple[Level, str] | None:
    """A shell reading a script from curl/wget, e.g. curl -fsSL URL | sh. Often an official
    installer, so destructive (warn) rather than catastrophic (block)."""
    if cmd.stdin_from in DOWNLOADERS:
        return "destructive", f"runs a script from {cmd.stdin_from} without reviewing it; download, read, then run"
    return None


RULES: dict[str, Rule] = {"rm": _rm, "dd": _dd, "chmod": _chmod_chown, "chown": _chmod_chown, "find": _find,
                          "mkfs": _format, "mkswap": _format, "wipefs": _format,
                          **dict.fromkeys(SHELLS, _shell)}

# Commands that run the command after them: name -> (options that take a value, positional
# arguments before the command). env also skips NAME=value; xargs adds arguments read from stdin.
WRAPPERS: dict[str, tuple[frozenset[str], int]] = {
    "sudo": (frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}), 0),
    "doas": (frozenset({"-u", "-C"}), 0),
    "pkexec": (frozenset({"--user"}), 0),
    "env": (frozenset({"-u", "-C", "-S"}), 0),
    "nice": (frozenset({"-n"}), 0),
    "ionice": (frozenset({"-c", "-n", "-p"}), 0),
    "nohup": (frozenset(), 0),
    "command": (frozenset(), 0),
    "builtin": (frozenset(), 0),
    "exec": (frozenset({"-a"}), 0),
    "stdbuf": (frozenset({"-i", "-o", "-e"}), 0),
    "timeout": (frozenset({"-s", "-k"}), 1),  # DURATION
    "chroot": (frozenset(), 1),  # NEWROOT
    "xargs": (frozenset({"-I", "-d", "-n", "-P", "-L", "-s", "-E", "-a"}), 0),
}
STDIN_ARGUMENTS = Word("<arguments from stdin>", frozenset({"commandsubstitution"}), "")


def _script(cmd: SimpleCommand) -> str | None:
    """The script a command runs from a string: sh -c '...', su -c '...', eval ..., watch ..."""
    texts = [w.text for w in cmd.words]
    if cmd.name in SHELLS or cmd.name == "su":
        for i, t in enumerate(texts[1:], start=1):
            if (re.fullmatch(r"-[a-zA-Z]*c", t) or t == "--command") and i + 1 < len(texts):
                return texts[i + 1]
        return None
    if cmd.name == "eval":
        return " ".join(texts[1:])
    if cmd.name == "watch":
        rest = texts[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[2:] if rest[0] in ("-n", "-d", "--interval") else rest[1:]
        return " ".join(rest) or None
    return None


def unwrap(cmd: SimpleCommand) -> list[SimpleCommand]:
    """The command(s) that really run: through wrappers (sudo, env, nohup, xargs, ...) and into
    scripts given as strings (sh -c, eval). [cmd] if not wrapped; [] for e.g. `sudo -i`."""
    if cmd.name in WRAPPERS:
        valued, positional = WRAPPERS[cmd.name]
        args, i = cmd.words[1:], 0
        while i < len(args):
            t = args[i].text
            if t == "--":
                i += 1
                break
            if cmd.name == "env" and re.fullmatch(r"\w+=.*", t):
                i += 1
            elif t.startswith("-") and len(t) > 1:
                i += 2 if t in valued else 1
            else:
                break
        rest = args[i + positional:]
        if not rest:
            return []
        if cmd.name == "xargs":
            rest = rest + (STDIN_ARGUMENTS,)
        return unwrap(SimpleCommand(rest, (), cmd.stdin_from))
    script = _script(cmd)
    if script is not None:
        return [c for inner in commands(script) for c in unwrap(inner)]
    return [cmd]


def commands(cmd: str) -> list[SimpleCommand]:
    """parse_commands, falling back to approximate_commands for syntax bashlex rejects.
    Raises ValueError if even the fallback fails (unbalanced quotes)."""
    try:
        return parse_commands(cmd)
    except (bashlex.errors.ParsingError, NotImplementedError):
        return approximate_commands(cmd)


def _rule_for(name: str) -> Rule | None:
    """The rule for a command name; mkfs.ext4, mkfs.xfs, ... share the mkfs rule."""
    return RULES.get(name) or (RULES["mkfs"] if name.startswith("mkfs.") else None)


def find_dangers(cmd: str) -> list[Finding]:
    """Every dangerous command in a command line, worst first (ties keep execution order).
    All findings are returned, not just the worst, so a reviser can fix them in one pass."""
    try:
        parsed = commands(cmd)
    except ValueError:  # not even shlex can split it: never report "safe" for what was not read
        return [Finding("destructive", cmd, "could not be parsed as shell (unbalanced quotes?); "
                                            "check it before running")]
    findings: list[Finding] = []

    def add(c: SimpleCommand, result: tuple[Level, str] | None) -> None:
        if result and Finding(result[0], c.text, result[1]) not in findings:
            findings.append(Finding(result[0], c.text, result[1]))

    for c in parsed:
        add(c, _redirect(c))  # redirects belong to the command as written
        for real in unwrap(c):
            rule = _rule_for(real.name)
            add(real, rule(real) if rule else None)
    return sorted(findings, key=lambda f: -SEVERITY[f.level])


def danger_level(cmd: str) -> Level | None:
    """The worst level in a command line, or None if nothing dangerous was found."""
    findings = find_dangers(cmd)
    return findings[0].level if findings else None
