"""Tests for the instance's first-boot script (infra/boot.sh.tftpl): rendered the way
Terraform's templatefile does it, then checked statically. It is never run here (it
installs packages and writes to /etc); its real test is a first boot on EC2."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "infra" / "boot.sh.tftpl"
COMPUTE = ROOT / "infra" / "compute.tf"
VARS = {
    "data_bucket": "ticket-agent-data-123456789012",
    "github_repo": "hsan512/linux-ticket-agent",
    "region": "us-east-1",
}


def render(variables: dict[str, str] = VARS) -> str:
    """templatefile: ${name} becomes the value, $${ becomes a literal ${."""
    source = TEMPLATE.read_text()
    out = re.sub(r"(?<!\$)\$\{(\w+)\}", lambda m: variables[m.group(1)], source)
    return out.replace("$${", "${")


def test_only_known_template_variables_are_used() -> None:
    """A shell ${name} written without the extra $ is a Terraform reference: plan
    fails with "Invalid reference", or worse, a name that exists gets substituted."""
    used = set(re.findall(r"(?<!\$)\$\{([^}]*)\}", TEMPLATE.read_text()))
    assert used <= set(VARS), f"unescaped ${{...}}: {sorted(used - set(VARS))}"
    assert "%{" not in TEMPLATE.read_text()  # template directives: none intended


def test_compute_tf_passes_exactly_these_variables() -> None:
    pattern = r'templatefile\("\$\{path\.module\}/boot\.sh\.tftpl",\s*\{(.*?)\}\)'
    passed = re.search(pattern, COMPUTE.read_text(), re.S)
    assert passed, "compute.tf no longer renders boot.sh.tftpl"
    names = set(re.findall(r"^\s*(\w+)\s*=", passed.group(1), re.M))
    assert names == set(VARS)


def test_values_land_where_expected() -> None:
    script = render()
    assert script.startswith("#!/bin/bash\n")  # cloud-init runs it by its shebang
    assert f'AWS_REGION="{VARS["region"]}"' in script
    assert f"https://github.com/{VARS['github_repo']}.git" in script
    assert f"s3://{VARS['data_bucket']}/qdrant_storage/" in script
    assert f"s3://{VARS['data_bucket']}/handoff/" in script


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="no shellcheck")
def test_rendered_script_passes_shellcheck(tmp_path: Path) -> None:
    rendered = tmp_path / "boot.sh"
    rendered.write_text(render())
    result = subprocess.run(
        ["shellcheck", str(rendered)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


def test_rendered_script_parses() -> None:
    result = subprocess.run(
        ["bash", "-n"], input=render(), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_strict_mode() -> None:
    assert "set -euxo pipefail" in render()


def test_docker_keeps_real_addresses_and_limits_logs() -> None:
    script = render()
    assert '"userland-proxy": false' in script  # /metrics private-address rule
    assert '"max-size": "10m"' in script and '"max-file": "3"' in script
    # written before Docker is installed, so the daemon starts with it
    assert script.index("/etc/docker/daemon.json") < script.index("install docker-ce")


def test_compose_comes_from_dockers_repository() -> None:
    script = render()
    assert "download.docker.com/linux/ubuntu" in script
    assert "docker-compose-plugin" in script  # >= 2.24.4 for !override


def test_apt_waits_for_the_lock() -> None:
    assert "DPkg::Lock::Timeout" in render()


def test_handoff_folder_belongs_to_the_api_user() -> None:
    """The API container runs as uid 1000; a root-owned folder fails every hand-off."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "useradd -m -u 1000 appuser" in dockerfile and "USER appuser" in dockerfile
    script = render()
    assert "chown -R 1000:1000 data/handoff" in script
    assert script.index('handoff/" data/handoff/') < script.index("chown -R 1000:1000")


def test_empty_qdrant_index_fails_the_boot() -> None:
    script = render()
    assert re.search(r"ls -A data/qdrant_storage.*\n.*fail ", script)


def test_deploys_the_release_sha_from_ssm() -> None:
    script = render()
    assert "--name /ticket-agent/RELEASE_SHA" in script
    deploy = '"$APP_DIR/deploy/deploy.sh" "$sha"'
    assert deploy in script
    assert script.index("RELEASE_SHA") < script.index(deploy)
    # data must be in place before deploy.sh checks for it
    assert script.index('qdrant_storage/" data/qdrant_storage/') < script.index(deploy)


def test_no_secrets_handled_here() -> None:
    """-x prints every command into the boot log, so no secret may pass through."""
    script = render()
    for name in ("OPENAI_API_KEY", "GRAFANA_ADMIN_PASSWORD", "ORIGIN_SECRET"):
        assert name not in script
    assert "--with-decryption" not in script
