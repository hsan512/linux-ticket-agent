import gzip

import pytest

from agent.tools.command_check import (
    check_answer, check_command, command_names, commands_from_contents, defined_functions, syntax_error,
)


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("ls -la", ["ls"]),
        ("sudo apt-get install -y rsync", ["sudo", "apt-get"]),
        ("find . -name '*.log' | xargs -0 rm -f", ["find", "xargs", "rm"]),
        ("grep foo file.txt > out.txt && sort out.txt", ["grep", "sort"]),
        ("LC_ALL=C sort -u a b; echo done", ["sort", "echo"]),
        ("for f in *.txt; do wc -l \"$f\"; done", ["wc"]),
        ("if [ -f x ]; then cat x; fi", ["[", "cat"]),
        ("/usr/bin/env python3 -V", ["env", "python3"]),
        ("timeout 5s ping -c1 host", ["timeout", "ping"]),
        ("./run.sh --fast", []),
        ("$EDITOR file", []),
        ("awk '{print $1}' f  # comment | not a pipe", ["awk"]),
        ("find . -type f \\( -name '*.mp3' -o -name '*.wav' \\) -delete", ["find"]),
    ],
)
def test_command_names(cmd: str, expected: list[str]) -> None:
    assert command_names(cmd) == expected


def test_unbalanced_quotes_yield_no_names() -> None:
    assert command_names("echo 'oops") == []


def test_syntax_error_detected_without_execution(tmp_path) -> None:
    marker = tmp_path / "should_not_exist"
    assert syntax_error(f"touch {marker}") is None
    assert not marker.exists()  # bash -n parses only
    assert syntax_error("if true; then echo x") is not None
    assert syntax_error("cat <file>") is not None


def test_commands_from_contents(tmp_path) -> None:
    contents = tmp_path / "Contents-amd64.gz"
    with gzip.open(contents, "wt") as f:
        f.write("bin\t\t\tadmin/base-files\n"
                "usr/bin/fzf\t\t\tuniverse/utils/fzf\n"
                "usr/sbin/nft\t\t\tnet/nftables\n"
                "usr/share/doc/fzf/README\t\t\tuniverse/utils/fzf\n"
                "usr/lib/foo/bin/inner\t\t\tlibs/foo\n")
    assert commands_from_contents(contents) == {"fzf", "nft"}


def test_check_answer_allows_own_functions_and_placeholders() -> None:
    commands = ["fmt () { printf '%s\\n' \"$1\"; }", "function trim { :; }", "cat f | fmt | trim",
                "xargs -0 PDF_READER", "frob x"]
    checks = check_answer(commands, {"printf", "cat", "xargs", ":"})
    assert defined_functions(commands) == {"fmt", "trim"}
    assert [c.unknown_names for c in checks] == [[], [], [], [], ["frob"]]


def test_check_answer_accepts_names_from_ticket() -> None:
    checks = check_answer(["youtube_download URL", "frob"], set(), ticket="My script youtube_download fails")
    assert [c.unknown_names for c in checks] == [[], ["frob"]]


def test_check_command_flags_unknown() -> None:
    known = {"ls", "grep"}
    result = check_command("ls | grep x | frobnicate --all", known)
    assert result.syntax_error is None
    assert result.unknown_names == ["frobnicate"]
    assert not result.ok
    assert check_command("ls | grep x", known).ok
