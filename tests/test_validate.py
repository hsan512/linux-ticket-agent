import pytest

from agent.tools.packages import CommandPackageLookup
from agent.tools.validate import (
    Issue,
    command_names,
    context_names,
    format_issues,
    needs_lookup,
    syntax_issue,
    syntax_issues,
    validate_commands,
)

LOOKUP = CommandPackageLookup({"noble": {"rm": ["coreutils"], "nc.openbsd": ["netcat-openbsd"]}})


def test_syntax_issue() -> None:
    assert syntax_issue("ls -la") is None
    issue = syntax_issue("for f in *; do echo $f")
    assert issue is not None and issue.kind == "syntax" and issue.command == "for f in *; do echo $f"
    assert issue.message.startswith("bash -n: ")
    assert syntax_issue("touch /tmp/never") is None  # parsed only: valid, and nothing is run


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("ls -la", {"ls"}),
        ("sudo apt install nginx", {"sudo", "apt"}),              # the wrapper and what it runs
        ("nohup nice -n 10 rm -rf build", {"nohup", "rm"}),       # inner wrappers are always real commands
        ("sudo sh -c 'mkfs.ext4 x && rm y'", {"sudo", "mkfs.ext4", "rm"}),
        ("find . | xargs rm", {"find", "xargs", "rm"}),
        ("echo $(date) > out.txt", {"date", "echo"}),
        ("[[ -f x ]] && cat x", {"cat"}),                         # fallback parser: [[ is not a command
        ("> file", set()),                                         # bare redirect: no command
        ("echo \"unterminated", set()),                            # cannot be split: syntax_issue reports it
    ],
)
def test_command_names(line: str, names: set[str]) -> None:
    assert command_names(line) == names


def test_context_names() -> None:
    ticket = "Cron job fails: my script backup_db.sh works by hand, but cron says pg_dump: command not found"
    commands = ["mkcd() { mkdir -p \"$1\" && cd \"$1\"; }", "mkcd ~/x", "backup_db.sh --verbose"]
    names = context_names(commands, ticket)
    assert {"backup_db.sh", "pg_dump", "mkcd"} <= names
    assert "ifconfg" not in names


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("cd", False),          # bash builtin: no package ships it
        ("export", False),
        ("setopt", False),      # zsh builtin
        ("PDF_READER", False),  # placeholder by prompt convention
        ("backup_db.sh", False),  # mentioned in the ticket
        ("ifconfg", True),      # must be looked up
        ("rm", True),           # real, but its existence comes from the package table
        ("awk", False),         # alternatives: no package lists the name
        ("iptables", False),
        ("/home/me/.pyenv/bin/python", False),  # paths and variables are not names to look up
        ("./deploy.sh", False),
        ("$PDF_READER", False),
        ("$(dircolors)", False),
        ("*", False),           # parser noise
        ("sd*", False),
        ("@reboot", False),
        ("ips+=", False),
        ("\n", False),
        ("grub-install", True),
        ("gcc-13", True),
        ("g++", True),
    ],
)
def test_needs_lookup(name: str, expected: bool) -> None:
    assert needs_lookup(name, {"backup_db.sh"}) is expected


def test_lookup_packages() -> None:
    assert LOOKUP.packages("rm") == {"noble": ["coreutils"]}
    assert LOOKUP.packages("nc") == {"noble": ["netcat-openbsd"]}  # alternatives fallback
    assert LOOKUP.packages("ifconfg") == {}
    assert Issue("x", "unknown_command", "m") == Issue("x", "unknown_command", "m")


TABLE = CommandPackageLookup({"noble": {"rm": ["coreutils"], "ls": ["coreutils"], "mkdir": ["coreutils"],
                                        "sudo": ["sudo"], "cat": ["coreutils"]}})


@pytest.mark.parametrize(
    ("commands", "ticket", "kinds"),
    [
        (["ls -la"], "", []),
        (["for f in *; do echo $f"], "", ["syntax"]),
        (["ifconfg -a"], "", ["unknown_command"]),                     # no ticket: names are still checked
        (["ifconfg -a"], "my ifconfg is broken", []),                  # the asker mentioned it
        (["cd /tmp && export X=1"], "", []),                           # builtins
        (["sudo rm -rf /"], "", ["catastrophic"]),
        (["rm -rf ./build", "frobnicate"], "", ["destructive", "unknown_command"]),
        (['mkcd() { mkdir -p "$1"; }', "mkcd x"], "", []),             # a function the answer defines
        (["for f in *; do echo $f", "frobnicate"], "", ["syntax", "unknown_command"]),  # every line is checked
        (["for f in *; do frobnicate $f"], "", ["syntax", "unknown_command"]),  # a line with a syntax error still gets name checks
        (["while read -r x; do", "  cat $x", "done < list"], "", []),  # one script split over items is valid
    ],
)
def test_validate_commands(commands: list[str], ticket: str, kinds: list[str]) -> None:
    assert [i.kind for i in validate_commands(commands, TABLE, ticket)] == kinds


def test_validate_commands_messages_name_the_problem() -> None:
    [unknown] = validate_commands(["ls && frobnicate -x"], TABLE)
    assert unknown.command == "ls && frobnicate -x" and "frobnicate" in unknown.message
    [danger] = validate_commands(["cd /tmp && rm -rf /"], TABLE)
    assert danger.command == "cd /tmp && rm -rf /" and danger.message.startswith("rm -rf /: ")  # which sub-command


def test_syntax_issues_accepts_a_script_split_over_items() -> None:
    split = ["while read -r x; do", "  cat $x", "done < list"]
    assert syntax_issue(split[0]) is not None           # each fragment alone is invalid...
    assert syntax_issues(split) == []                    # ...but together they are one valid script
    broken = ["ls", "for f in *; do echo $f"]
    assert [i.command for i in syntax_issues(broken)] == ["for f in *; do echo $f"]  # invalid as a whole: per item
    assert syntax_issues([]) == []


def test_format_issues() -> None:
    issues = [Issue("rm -rf $D/", "catastrophic", "rm -rf /: recursive delete of /"),
              Issue("frob -x", "unknown_command", "frob: not supported by any known Ubuntu package")]
    assert format_issues(issues) == ("- `rm -rf $D/` (catastrophic): rm -rf /: recursive delete of /\n"
                                     "- `frob -x` (unknown_command): frob: not supported by any known Ubuntu package")
    assert format_issues([]) == ""
