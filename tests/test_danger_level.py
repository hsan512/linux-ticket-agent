import bashlex.errors
import pytest

from agent.tools import danger_level as dl
from agent.tools.danger_level import Finding, SimpleCommand, Word, find_dangers, parse_commands


def W(text: str, *expansions: str) -> Word:  # noqa: N802 (short name keeps the table readable)
    return Word(text, frozenset(expansions))


def cmd(*words: str | Word, redirects: tuple[tuple[str, str | Word], ...] = ()) -> SimpleCommand:
    """Plain strings are literal words (no expansion)."""
    as_word = lambda w: w if isinstance(w, Word) else Word(w)  # noqa: E731
    return SimpleCommand(tuple(map(as_word, words)), tuple((op, as_word(t)) for op, t in redirects))


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # --- splitting and quoting ---
        ("ls -la", [cmd("ls", "-la")]),
        ("sudo rm -rf \"$DIR\"/* && echo done",                                # quotes removed, split on &&
         [cmd("sudo", "rm", "-rf", W("$DIR/*", "parameter")), cmd("echo", "done")]),
        ("a; b || c & d", [cmd("a"), cmd("b"), cmd("c"), cmd("d")]),         # every list operator
        ("find . -name x | xargs rm -rf",                                     # pipeline; wrappers kept
         [cmd("find", ".", "-name", "x"), cmd("xargs", "rm", "-rf")]),
        ("grep -r \"rm -rf /\" .", [cmd("grep", "-r", "rm -rf /", ".")]),    # quoted text is one argument
        # --- nested commands run too ---
        ("echo $(rm -rf /)", [cmd("rm", "-rf", "/"), cmd("echo", W("$(rm -rf /)", "commandsubstitution"))]),  # inner first
        ("echo \"$(rm -rf /)\"",                                              # double quotes still substitute
         [cmd("rm", "-rf", "/"), cmd("echo", W("$(rm -rf /)", "commandsubstitution"))]),
        ("echo '$(rm -rf /)'", [cmd("echo", "$(rm -rf /)")]),                # single quotes: plain text
        ("echo `ls`", [cmd("ls"), cmd("echo", W("`ls`", "commandsubstitution"))]),  # backticks
        ("diff <(ls a) <(ls b)",                                              # process substitution
         [cmd("ls", "a"), cmd("ls", "b"),
          cmd("diff", W("<(ls a)", "processsubstitution"), W("<(ls b)", "processsubstitution"))]),
        ("if true; then rm -rf /; fi", [cmd("true"), cmd("rm", "-rf", "/")]),
        ("for f in *.log; do rm \"$f\"; done", [cmd("rm", W("$f", "parameter"))]),
        ("{ rm x; }", [cmd("rm", "x")]),                                      # group
        ("f() { rm x; }", [cmd("rm", "x")]),                                  # function body, even if never called
        # --- redirections ---
        ("cat image.iso > /dev/sda", [cmd("cat", "image.iso", redirects=((">", "/dev/sda"),))]),
        ("cmd 2>&1 >/dev/null", [cmd("cmd", redirects=((">", "/dev/null"),))]),  # fd duplication has no file
        ("> /dev/sda", [cmd(redirects=((">", "/dev/sda"),))]),               # redirect with no command still writes
        ("echo x > $(mktemp)",                                                # substitution inside a redirect target
         [cmd("mktemp"), cmd("echo", "x", redirects=((">", W("$(mktemp)", "commandsubstitution")),))]),
        ("cat <<< $(rm -rf /)",
         [cmd("rm", "-rf", "/"), cmd("cat", redirects=(("<<<", W("$(rm -rf /)", "commandsubstitution")),))]),
        # --- variables vs. literals vs. substitutions: same text, different meaning ---
        ("rm -rf $HOME", [cmd("rm", "-rf", W("$HOME", "parameter"))]),
        ("rm -rf \"$HOME\"", [cmd("rm", "-rf", W("$HOME", "parameter"))]),     # double quotes still expand
        ("rm -rf '$HOME'", [cmd("rm", "-rf", "$HOME")]),                       # a directory literally named $HOME
        ("rm -rf ${HOME}", [cmd("rm", "-rf", W("${HOME}", "parameter"))]),
        ("rm -rf ~ ~/x", [cmd("rm", "-rf", W("~", "tilde"), W("~/x", "tilde"))]),
        ("rm -rf '~'", [cmd("rm", "-rf", "~")]),                               # quoted tilde is not expanded
        ("rm -rf $(HOME)", [cmd("HOME"), cmd("rm", "-rf", W("$(HOME)", "commandsubstitution"))]),  # runs a command named HOME
    ],
)
def test_parse_commands(line: str, expected: list[SimpleCommand]) -> None:
    assert parse_commands(line) == expected


def test_parse_commands_raises_on_invalid_syntax() -> None:
    with pytest.raises(bashlex.errors.ParsingError):  # callers must not treat "unparsable" as "safe"
        parse_commands('echo "unterminated')


@pytest.mark.parametrize(
    ("line", "name", "options", "operands"),
    [
        ("rm -rf build dist", "rm", ("-rf",), ("build", "dist")),
        ("rm build -r -f /", "rm", ("-r", "-f"), ("build", "/")),       # options anywhere
        ("rm -- -weird /", "rm", (), ("-weird", "/")),                   # after "--" everything is an operand
        ("find . -name x -delete", "find", ("-name", "-delete"), (".", "x")),  # syntax only: x is -name's value
        ("> /dev/sda", "", (), ()),
    ],
)
def test_name_options_operands(line: str, name: str, options: tuple[str, ...], operands: tuple[str, ...]) -> None:
    c = parse_commands(line)[0]
    assert (c.name, c.options, tuple(w.text for w in c.operands)) == (name, options, operands)


def test_find_dangers_reports_all_findings_worst_first(monkeypatch: pytest.MonkeyPatch) -> None:
    # a stand-in rule, so this tests the collection logic, not real rules
    def fake_rm(c: SimpleCommand) -> tuple[str, str] | None:
        if "/" in [w.text for w in c.operands]:
            return "catastrophic", "deletes /"
        return "destructive", "deletes files"

    monkeypatch.setattr(dl, "RULES", {"rm": fake_rm})
    findings = find_dangers("rm a && echo $(rm -rf /) && rm -rf / && rm a")
    assert findings == [Finding("catastrophic", "rm -rf /", "deletes /"),  # same finding twice: kept once
                        Finding("destructive", "rm a", "deletes files")]   # worst first
    assert dl.danger_level("rm a") == "destructive"
    assert dl.danger_level("ls -la") is None


@pytest.mark.parametrize(
    ("line", "worst_case"),
    [
        # every variable and substitution replaced by what it gives when empty, then quotes removed
        ("rm -rf $DIR/*", "/*"),
        ("rm -rf \"$DIR\"/*", "/*"),
        ("rm -rf ${DIR}/*", "/*"),
        ("rm -rf ${DIR:?}/*", "${DIR:?}/*"),       # bash stops if DIR is empty or unset: kept as is
        ("rm -rf ${DIR?}/*", "/*"),                # without ":" bash only stops if DIR is unset
        ("rm -rf ${DIR:-/tmp/x}/*", "/tmp/x/*"),   # ":-" uses the default when empty
        ("rm -rf ${DIR-/tmp/x}/*", "/*"),          # "-" uses it only when unset; empty stays empty
        ("rm -rf ${DIR:-}/*", "/*"),
        ("rm -rf /$(true)", "/"),
        ("rm -rf /usr/$(x)", "/usr/"),
        ("rm -rf /srv/$(date +%F)", "/srv/"),
        ("rm -rf $(get $(x))/etc", "/etc"),       # nested substitution removed as one
        ("rm -rf `pwd`/*", "/*"),
        ("rm -rf '/a b'/$(x)", "/a b/"),          # quotes outside the substitution still removed
        ("rm -rf $(HOME)", ""),
        ("rm -rf $HOME", ""),                      # home is the rules' job: they also see text and expansions
        ("rm -rf ~/x", "~/x"),                     # tilde kept for the same reason
        ("rm -rf '$HOME'", "$HOME"),               # literal: nothing to expand
        ("rm -rf ./build", "./build"),
    ],
)
def test_worst_case(line: str, worst_case: str) -> None:
    assert parse_commands(line)[-1].words[-1].worst_case == worst_case


@pytest.mark.parametrize(
    ("line", "level"),
    [
        # --- catastrophic: recursive delete of /, a system directory, or home ---
        ("rm -rf /", "catastrophic"),
        ("rm -rf /*", "catastrophic"),                       # glob: --preserve-root does not help
        ("rm -rf /usr/", "catastrophic"),
        ("rm -rf /tmp/../usr", "catastrophic"),              # ".." resolved
        ("rm -rf abc /", "catastrophic"),                    # any operand counts
        ("rm abc / -rf", "catastrophic"),                    # options after operands (GNU)
        ("rm -r -f build /etc", "catastrophic"),
        ("rm -fR /etc", "catastrophic"),
        ("rm -f -r /etc", "catastrophic"),
        ("rm --recursive /etc", "catastrophic"),
        ("rm -rf --no-preserve-root /", "catastrophic"),
        ("rm -rf --no-preserve-root build", "catastrophic"), # the flag alone is a red flag
        ("rm -rf ~", "catastrophic"),
        ("rm -rf ~/*", "catastrophic"),
        ("rm -rf $HOME", "catastrophic"),
        ("rm -rf ${HOME}/", "catastrophic"),
        ("rm -rf $HOME/*", "catastrophic"),
        ("rm -rf /$HOME", "catastrophic"),                   # HOME set: whole home; empty: /
        ("rm -rf $HOME/abc /etc", "catastrophic"),           # a harmless first operand does not hide /etc
        # --- catastrophic: an empty expansion would turn the target into / or a system directory ---
        ("rm -rf $DIR/*", "catastrophic"),
        ("rm -rf ${DIR?}/*", "catastrophic"),                # "?" without ":" lets an empty DIR through
        ("rm -rf $(HOME)/", "catastrophic"),
        ("rm -rf /usr/$(x)", "catastrophic"),
        ("rm -rf `pwd`/*", "catastrophic"),
        ("rm -rf build $DIR/*", "catastrophic"),
        # --- destructive: data loss in a limited (or unknown) scope ---
        ("rm -rf ${DIR:?}/*", "destructive"),                # bash stops if DIR is empty
        ("rm -rf '$HOME'", "destructive"),                   # literal directory named $HOME
        ("rm -rf $(HOME)", "destructive"),                   # target is a command's output: unknown
        ("rm -rf $HOME/abc", "destructive"),
        ("rm -rf ~/.cache", "destructive"),
        ("rm -rf $HOMEDIR", "destructive"),                  # a different variable, not HOME
        ("rm -rf /srv/$(date +%F)", "destructive"),
        ("rm -rf ./build", "destructive"),
        ("rm -rf /usr/local/myapp", "destructive"),
        ("rm -rf build dist", "destructive"),
        ("rm notes.txt", "destructive"),
        ("rm /etc", "destructive"),                          # not recursive: rm refuses a directory
        # --- nothing to delete ---
        ("rm -rf", None),
    ],
)
def test_rm_rule(line: str, level: str | None) -> None:
    result = dl._rm(parse_commands(line)[-1])
    assert (result[0] if result else None) == level


@pytest.mark.parametrize(
    ("path", "normalised"),
    [("/", "/"), ("/*", "/"), ("//*", "/"), ("/usr/", "/usr"), ("/usr/*", "/usr"),
     ("/tmp/../usr", "/usr"), ("./build", "build"), ("", ""), ("usr*", "usr*")],
)
def test_normalise(path: str, normalised: str) -> None:
    assert dl._normalise(path) == normalised


@pytest.mark.parametrize(
    ("line", "reason_part"),
    [  # reasons go to the reviser, so they must name the target and say why
        ("rm -rf /", "/ is / (the whole system)"),
        ("rm -rf /etc", "the system directory /etc"),
        ("rm -rf ~/*", "~/* is the whole home directory"),
        ("rm -rf $DIR/*", "$DIR/* becomes / (the whole system) if the variable or command output is empty"),
        ("rm -rf /usr/$(x)", "becomes the system directory /usr"),
        ("rm -rf --no-preserve-root build", "--no-preserve-root"),
        ("rm -rf build dist", "recursive delete of build dist"),
        ("rm notes.txt", "delete of notes.txt"),
    ],
)
def test_rm_reason(line: str, reason_part: str) -> None:
    assert reason_part in dl._rm(parse_commands(line)[-1])[1]


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("dd if=/dev/zero of=/dev/sda bs=1M", "catastrophic"),
        ("dd if=ubuntu.iso of=/dev/sdb status=progress", "catastrophic"),  # often the right answer, still risky
        ("dd if=x of=/dev/sdX", "catastrophic"),                            # placeholder for a disk
        ("dd if=x of=/dev/nvme0n1", "catastrophic"),
        ("dd if=x of=/dev/mmcblk0", "catastrophic"),
        ("dd if=x of=/dev/sda1", "catastrophic"),                           # a partition
        ("dd if=/dev/urandom of=$DEV", "catastrophic"),                     # unknown target
        ("dd if=/dev/sda of=backup.img", "destructive"),                    # reads the disk, overwrites a file
        ("dd if=/dev/zero of=/dev/null", None),
        ("dd if=x", None),                                                  # no of=: writes to stdout
    ],
)
def test_dd_rule(line: str, level: str | None) -> None:
    result = dl._dd(parse_commands(line)[-1])
    assert (result[0] if result else None) == level


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("mkfs.ext4 /dev/sdb1", "catastrophic"),
        ("mkfs -t xfs /dev/nvme0n1p2", "catastrophic"),
        ("mkswap /dev/sda2", "catastrophic"),
        ("wipefs -a /dev/sdb", "catastrophic"),
        ("mkfs.vfat $PART", "catastrophic"),        # unknown target
        ("mkfs.ext4 disk.img", "destructive"),      # an image file, not a disk
        ("mkfs.ext4 --help", None),
    ],
)
def test_format_rules(line: str, level: str | None) -> None:
    assert dl.danger_level(line) == level      # through danger_level, so mkfs.* name lookup is covered


@pytest.mark.parametrize(
    ("line", "level"),
    [
        ("cat image.iso > /dev/sda", "catastrophic"),
        ("echo x >> /dev/nvme0n1", "catastrophic"),
        ("> /dev/sdb1", "catastrophic"),            # no command, still truncates the partition
        ("pv image.img > $DEV", "catastrophic"),
        ("echo x > /dev/null", None),
        ("cmd 2>&1 > /dev/null", None),
        ("ls > out.txt", None),
        ("cat /dev/sda > disk.img", None),           # reads the disk; writing a new file is not flagged
    ],
)
def test_redirect_rule(line: str, level: str | None) -> None:
    assert dl.danger_level(line) == level


@pytest.mark.parametrize(
    ("line", "level"),
    [
        # --- wrappers run the command after them ---
        ("sudo rm -rf /", "catastrophic"),
        ("sudo -u root rm -rf /", "catastrophic"),            # "root" is -u's value, not the command
        ("sudo -E dd if=x of=/dev/sda", "catastrophic"),
        ("env X=1 rm -rf /", "catastrophic"),
        ("nohup nice -n 10 rm -rf /", "catastrophic"),        # nested wrappers
        ("timeout 5s rm -rf /", "catastrophic"),              # timeout's DURATION is skipped
        ("sudo sh -c 'rm -rf /'", "catastrophic"),            # a script given as a string
        ("bash -c \"mkfs.ext4 /dev/sdb1\"", "catastrophic"),
        ("eval 'rm -rf /'", "catastrophic"),
        ("find . -name x | xargs rm -rf", "destructive"),      # targets come from stdin
        ("find . -print0 | xargs -0 -I {} rm {}", "destructive"),
        ("sudo -i", None),                                     # opens a shell, runs nothing here
        ("sudo apt install nginx", None),
        ("sudo", None),
        ("xargs", None),
        # --- syntax bashlex rejects: approximate fallback ---
        ("time rm -rf /", "catastrophic"),
        ("time sudo dd if=x of=/dev/sda", "catastrophic"),
        ("[[ -d /x ]] && rm -rf /", "catastrophic"),
        ("echo $((1+2)); rm -rf $DIR/*", "catastrophic"),
        ("case $x in a) rm -rf /;; esac", "catastrophic"),
        ("arr=(a b); rm \"${arr[@]}\"", "destructive"),
        ("[[ -f x ]] && echo ok", None),
        ("[[ -n x ]] && echo '$(rm -rf /)'", "catastrophic"),  # fallback cannot see single quotes: a safe false alarm
        ("echo \"unterminated", "destructive"),                # unparsable is never reported as safe
        # --- chmod / chown ---
        ("chmod -R 777 /", "catastrophic"),
        ("sudo chown -R user:user /usr", "catastrophic"),
        ("chmod -R 777 /var/www", "destructive"),             # world-writable: a security hole
        ("chmod -R a+w ./site", "destructive"),
        ("chmod +x script.sh", None),
        ("chmod -R 755 ./dist", None),
        ("chown -R www-data:www-data /var/www/app", None),
        ("chmod 777 /etc", None),                              # one directory, not recursive
        # --- find ---
        ("find / -name core -delete", "catastrophic"),        # matches could be anywhere
        ("find ~ -name x -delete", "catastrophic"),
        ("find . -name \"*.tmp\" -delete", "destructive"),
        ("find /var/log -mtime +7 -exec rm {} \;", "destructive"),
        ("find . -name x -print", None),
        # --- download piped into a shell: warn, it is often an official installer ---
        ("curl -fsSL https://x.sh | sh", "destructive"),
        ("curl -fsSL https://x.sh | sudo bash", "destructive"),
        ("wget -qO- https://x | bash -s", "destructive"),
        ("cat script.sh | bash", None),
        ("bash install.sh", None),
    ],
)
def test_danger_level_end_to_end(line: str, level: str | None) -> None:
    assert dl.danger_level(line) == level


def test_findings_name_the_real_command() -> None:
    findings = find_dangers("sudo sh -c 'rm -rf /tmp/x && rm -rf /'")
    assert [(f.level, f.command) for f in findings] == [            # the commands inside, not "sudo sh -c ..."
        ("catastrophic", "rm -rf /"), ("destructive", "rm -rf /tmp/x")]  # both reported, worst first
    assert find_dangers("curl -fsSL https://x.sh | sudo bash")[0].reason.startswith("runs a script from curl")


@pytest.mark.parametrize("line", ["#!/bin/bash", "# just a note"])
def test_comment_only_line_is_harmless(line: str) -> None:
    assert dl.commands(line) == [] and find_dangers(line) == []       # bashlex itself crashes on these
