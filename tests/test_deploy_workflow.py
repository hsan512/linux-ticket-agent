"""Tests for the CD workflow (.github/workflows/deploy.yml): it releases exactly the
commit CI tested, with the least permissions, and agrees with the files it depends on
(ci.yml's name, the compose image, the boot script's path, Terraform's outputs)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
WORKFLOW = yaml.safe_load(TEXT)
JOBS = WORKFLOW["jobs"]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())


def steps(job: str) -> list[dict]:
    return JOBS[job]["steps"]


def step(job: str, uses_or_name: str) -> dict:
    return next(
        s
        for s in steps(job)
        if uses_or_name in s.get("uses", "") or uses_or_name in s.get("name", "")
    )


def test_runs_only_after_ci_passes_on_a_push_to_main() -> None:
    trigger = WORKFLOW[True]  # YAML 1.1 reads the key `on` as True
    assert list(trigger) == ["workflow_run"]
    run = trigger["workflow_run"]
    assert run["workflows"] == [CI["name"]]  # the name inside ci.yml, not its filename
    assert run["types"] == ["completed"] and run["branches"] == ["main"]
    condition = JOBS["prepare"]["if"]
    assert "conclusion == 'success'" in condition and "event == 'push'" in condition
    assert JOBS["publish"]["needs"] == "prepare"
    assert set(JOBS["release"]["needs"]) == {"prepare", "publish"}


def test_releases_the_commit_ci_tested() -> None:
    assert WORKFLOW["env"]["SHA"] == "${{ github.event.workflow_run.head_sha }}"
    # never used in an expression: it is the newest commit on main, maybe untested
    assert not re.search(r"\$\{\{\s*github\.sha", TEXT)
    for job in ("publish", "release"):
        assert step(job, "actions/checkout")["with"]["ref"] == "${{ env.SHA }}"
    build = step("publish", "docker/build-push-action")["with"]
    assert build["context"] == "."  # the checkout at SHA, not the event's commit
    assert build["tags"] == "${{ env.IMAGE }}:${{ env.SHA }}"


def test_least_privilege_per_job() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read"}
    assert JOBS["publish"]["permissions"] == {"contents": "read", "packages": "write"}
    assert JOBS["release"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert JOBS["release"]["environment"] == "production"  # gh-deploy trusts only this


def test_never_overlaps() -> None:
    assert WORKFLOW["concurrency"]["cancel-in-progress"] is False


def test_image_matches_the_production_compose_file() -> None:
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    assert f"image: {WORKFLOW['env']['IMAGE']}:${{IMAGE_TAG" in compose
    assert WORKFLOW["env"]["IMAGE"] == WORKFLOW["env"]["IMAGE"].lower()


def test_release_recorded_after_publish_and_before_deploy() -> None:
    names = [s.get("name", "") for s in steps("release")]
    record = names.index("Record the release")
    assert names.index("Deploy on the instance") > record
    assert "--name /ticket-agent/RELEASE_SHA" in steps("release")[record]["run"]
    boot = (ROOT / "infra" / "boot.sh.tftpl").read_text()
    assert "--name /ticket-agent/RELEASE_SHA" in boot  # the boot script reads it


def test_deploy_runs_deploy_sh_on_the_instance_only_when_one_runs() -> None:
    deploy = step("release", "Deploy on the instance")
    assert deploy["if"] == "steps.instance.outputs.id != ''"
    assert "aws ssm send-command" in deploy["run"]
    assert "/opt/ticket-agent/deploy/deploy.sh $SHA" in deploy["run"]
    boot = (ROOT / "infra" / "boot.sh.tftpl").read_text()
    assert (
        "APP_DIR=/opt/ticket-agent" in boot
    )  # the same path the boot script clones to
    assert "Values=ticket-agent" in step("release", "Find the running instance")["run"]
    assert "exit 1" in deploy["run"]  # a failed deploy fails the job


def test_variables_come_from_terraform_outputs() -> None:
    outputs = set(
        re.findall(r'output "(\w+)"', (ROOT / "infra" / "outputs.tf").read_text())
    )
    for var in set(re.findall(r"vars\.(\w+)", TEXT)):
        assert var.lower() in outputs, var  # DEPLOY_ROLE_ARN <- deploy_role_arn
    region = re.search(
        r'variable "region".*?default\s*=\s*"([^"]+)"',
        (ROOT / "infra" / "variables.tf").read_text(),
        re.S,
    ).group(1)
    assert step("release", "configure-aws-credentials")["with"]["aws-region"] == region


def test_deploy_role_allows_what_the_release_job_does() -> None:
    iam = (ROOT / "infra" / "iam.tf").read_text()
    for action in (
        "ssm:PutParameter",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "cloudfront:CreateInvalidation",
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ec2:DescribeInstances",
    ):
        assert action in iam, action
