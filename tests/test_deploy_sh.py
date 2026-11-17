"""Tests for deploy/deploy.sh, run against fake aws, git, docker and curl placed first
on PATH: no AWS account, Docker, or network needed. Each fake appends its arguments to
calls.log, and environment variables make it fail on purpose."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"
SECRETS = {
    "OPENAI_API_KEY": "sk-test-key",
    "GRAFANA_ADMIN_PASSWORD": "pa$$word",  # "$" must survive compose interpolation
    "ORIGIN_SECRET": "origin-secret-value",
}

FAKES = {
    # aws ssm get-parameter --name /ticket-agent/NAME ... -> prints the value
    "aws": """
        echo "aws $*" >> "$CALLS"
        name="${4##*/}"
        [[ "${FAIL_SSM:-}" == "$name" ]] && { echo "AccessDenied" >&2; exit 254; }
        case "$name" in
          OPENAI_API_KEY) echo "${OPENAI_VALUE-sk-test-key}" ;;
          GRAFANA_ADMIN_PASSWORD) echo 'pa$$word' ;;
          ORIGIN_SECRET) echo "origin-secret-value" ;;
        esac
    """,
    "git": """
        echo "git $*" >> "$CALLS"
        [[ "$1" == checkout && -n "${FAIL_CHECKOUT:-}" ]] && exit 128
        exit 0
    """,
    "docker": """
        echo "docker $*" >> "$CALLS"
        [[ " $* " == *" pull "* && -n "${FAIL_PULL:-}" ]] && exit 1
        [[ " $* " == *" logs "* ]] && echo "api log line"
        exit 0
    """,
    "curl": """
        echo "curl $*" >> "$CALLS"
        [[ -n "${UNHEALTHY:-}" ]] && exit 7
        exit 0
    """,
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fake repo root: deploy/deploy.sh plus the Qdrant data folder."""
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "deploy" / "deploy.sh")
    (root / "data" / "qdrant_storage").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        fake = bin_dir / name
        fake.write_text("#!/usr/bin/env bash\n" + body)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    return root


def deploy(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full_env = {
        **os.environ,
        "PATH": f"{repo.parent / 'bin'}:{os.environ['PATH']}",
        "CALLS": str(repo.parent / "calls.log"),
        "LOCK_FILE": str(repo.parent / "deploy.lock"),
        "HEALTH_TIMEOUT_S": "1",
        "HEALTH_INTERVAL_S": "0.1",
        **env,
    }
    return subprocess.run(
        ["bash", str(repo / "deploy" / "deploy.sh"), *args],
        cwd="/",  # the script must find the repo from its own location
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def calls(repo: Path) -> list[str]:
    log = repo.parent / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)  # SSM and the boot script run it directly


# --- usage -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [(), ("main",), (SHA[:7],), (SHA.upper(),), (SHA + "0",), (SHA, "extra")],
)
def test_bad_usage_exits_2_and_touches_nothing(repo: Path, args: tuple) -> None:
    result = deploy(repo, *args)
    assert result.returncode == 2 and "usage:" in result.stderr
    assert calls(repo) == [] and not (repo / ".env").exists()


# --- success ---------------------------------------------------------------------


def test_successful_deploy(repo: Path) -> None:
    result = deploy(repo, SHA)
    assert result.returncode == 0, result.stderr
    assert f"deployed {SHA}" in result.stdout
    log = calls(repo)
    assert f"git checkout --quiet --detach {SHA}" in log
    compose = "docker compose -f docker-compose.yml -f docker-compose.prod.yml"
    order = [
        next(i for i, c in enumerate(log) if c.startswith(prefix))
        for prefix in (
            "git fetch",
            "git checkout",
            "aws ssm",
            f"{compose} pull",
            f"{compose} up -d --remove-orphans",
            "curl",
            "docker image prune -f",
        )
    ]
    assert order == sorted(order), log


def test_env_file_has_secrets_and_tag_and_is_private(repo: Path) -> None:
    assert deploy(repo, SHA).returncode == 0
    env = repo / ".env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    lines = env.read_text().splitlines()
    expected = [f"{k}='{v}'" for k, v in SECRETS.items()] + [f"IMAGE_TAG='{SHA}'"]
    assert lines == expected  # single quotes: compose keeps "$$" literally
    assert not (repo / ".env.tmp").exists()


def test_reads_each_secret_decrypted_from_ssm(repo: Path) -> None:
    assert deploy(repo, SHA).returncode == 0
    ssm = [c for c in calls(repo) if c.startswith("aws ssm get-parameter")]
    assert [c.split()[4] for c in ssm] == [f"/ticket-agent/{k}" for k in SECRETS]
    assert all("--with-decryption" in c for c in ssm)


def test_secrets_never_printed(repo: Path) -> None:
    result = deploy(repo, SHA)
    for value in SECRETS.values():
        assert value not in result.stdout + result.stderr


def test_health_check_uses_localhost(repo: Path) -> None:
    assert deploy(repo, SHA).returncode == 0
    assert any("http://localhost:8000/health" in c for c in calls(repo))


# --- failures --------------------------------------------------------------------


def test_missing_qdrant_data_fails_before_anything_starts(repo: Path) -> None:
    (repo / "data" / "qdrant_storage").rmdir()
    result = deploy(repo, SHA)
    assert result.returncode == 1 and "qdrant_storage" in result.stderr
    assert not any(c.startswith(("aws", "docker")) for c in calls(repo))


def test_checkout_failure_stops_the_deploy(repo: Path) -> None:
    result = deploy(repo, SHA, FAIL_CHECKOUT="1")
    assert result.returncode != 0
    assert not any(c.startswith(("aws", "docker")) for c in calls(repo))


@pytest.mark.parametrize("name", list(SECRETS))
def test_ssm_failure_keeps_the_old_env(repo: Path, name: str) -> None:
    (repo / ".env").write_text("OLD=1\n")
    result = deploy(repo, SHA, FAIL_SSM=name)
    assert result.returncode == 1 and name in result.stderr
    assert (repo / ".env").read_text() == "OLD=1\n"
    assert not (repo / ".env.tmp").exists()
    assert not any(c.startswith("docker") for c in calls(repo))


@pytest.mark.parametrize("value", ["", "it's", "two\nlines"])
def test_unsafe_secret_value_is_rejected(repo: Path, value: str) -> None:
    (repo / ".env").write_text("OLD=1\n")
    result = deploy(repo, SHA, OPENAI_VALUE=value)
    assert result.returncode == 1 and "OPENAI_API_KEY" in result.stderr
    assert (repo / ".env").read_text() == "OLD=1\n"


def test_pull_failure_does_not_restart_the_stack(repo: Path) -> None:
    result = deploy(repo, SHA, FAIL_PULL="1")
    assert result.returncode == 1 and "pull failed" in result.stderr
    assert not any(" up " in c for c in calls(repo))  # old containers keep running


def test_unhealthy_api_prints_logs_and_fails(repo: Path) -> None:
    result = deploy(repo, SHA, UNHEALTHY="1")
    assert result.returncode == 1 and "did not become healthy" in result.stderr
    assert "api log line" in result.stderr
    assert any("logs --tail 100 api" in c for c in calls(repo))
    assert not any("image prune" in c for c in calls(repo))
