"""Tests for the infra workflow (.github/workflows/infra.yml): up and down run only
when meant to, log in the way the gh-infra role trusts, never destroy the instance
before the hand-off backup, and agree with Terraform (outputs, region, version)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / ".github" / "workflows" / "infra.yml").read_text()
WORKFLOW = yaml.safe_load(TEXT)
JOBS = WORKFLOW["jobs"]
IAM = (ROOT / "infra" / "iam.tf").read_text()
VARIABLES = (ROOT / "infra" / "variables.tf").read_text()


def steps(job: str) -> list[dict]:
    return JOBS[job]["steps"]


def index_of(job: str, text: str) -> int:
    """Index of the first step whose name, uses or run contains text."""
    return next(
        i
        for i, s in enumerate(steps(job))
        if any(text in s.get(k, "") for k in ("name", "uses", "run"))
    )


def test_triggers_are_manual_and_a_daily_schedule() -> None:
    trigger = WORKFLOW[True]  # YAML 1.1 reads the key `on` as True
    assert set(trigger) == {"workflow_dispatch", "schedule"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["action"]["options"] == ["up", "down"]
    assert inputs["skip_backup"]["default"] is False
    assert trigger["schedule"] == [{"cron": "0 17 * * *"}]


def test_schedule_runs_down_and_never_up() -> None:
    up, down = JOBS["up"]["if"], JOBS["down"]["if"]
    assert "github.event_name == 'workflow_dispatch'" in up
    assert "inputs.action == 'up'" in up and "schedule" not in up
    assert "github.event_name == 'schedule'" in down
    assert "inputs.action == 'down'" in down


def test_login_matches_the_gh_infra_trust_policy() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read", "id-token": "write"}
    trusted = re.search(r"for_each = toset\(\[([^\]]+)\]\)", IAM).group(1)
    region = re.search(
        r'variable "region".*?default\s*=\s*"([^"]+)"', VARIABLES, re.S
    ).group(1)
    for job in ("up", "down"):
        assert JOBS[job]["environment"] == "infra"
        assert '"infra"' in trusted
        login = steps(job)[index_of(job, "configure-aws-credentials")]["with"]
        assert login["role-to-assume"] == "${{ vars.INFRA_ROLE_ARN }}"
        assert login["aws-region"] == region
    assert 'assume_role_policy   = data.aws_iam_policy_document.github_assume["infra"]' in IAM


def test_never_overlaps_and_runs_in_infra() -> None:
    assert WORKFLOW["concurrency"] == {"group": "infra", "cancel-in-progress": False}
    assert WORKFLOW["defaults"]["run"]["working-directory"] == "infra"


def test_terraform_version_and_flags() -> None:
    required = re.search(
        r'required_version\s*=\s*">=\s*([\d.]+)"',
        (ROOT / "infra" / "versions.tf").read_text(),
    ).group(1)
    for job in ("up", "down"):
        setup = steps(job)[index_of(job, "hashicorp/setup-terraform")]["with"]
        assert setup["terraform_wrapper"] is False  # else `output -raw` gets extra text
        version = tuple(map(int, setup["terraform_version"].split(".")))
        assert version >= tuple(map(int, required.split(".")))
    runs = [s.get("run", "") for job in ("up", "down") for s in steps(job)]
    applies = [r for r in runs if r.startswith("terraform apply")]
    assert len(applies) == 2
    assert all("-auto-approve" in a and "-input=false" in a for a in applies)
    assert "instance_enabled=true" in applies[0] and "instance_enabled=false" in applies[1]


def test_up_waits_for_health_not_the_page() -> None:
    """The page comes from S3 and answers 200 while the instance is still booting."""
    wait = steps("up")[index_of("up", "Wait until healthy")]["run"]
    assert 'curl -fsS "$URL/health"' in wait
    assert "exit 1" in wait
    assert index_of("up", "Wait until healthy") > index_of("up", "instance_enabled=true")
    log = steps("up")[index_of("up", "Print the boot log")]
    assert log["if"] == "failure()"
    assert "cloud-init-output.log" in log["run"]


def test_down_backs_up_before_destroying() -> None:
    backup = index_of("down", "Back up the hand-off queue")
    destroy = index_of("down", "instance_enabled=false")
    assert index_of("down", "Find the running instance") < backup < destroy
    step = steps("down")[backup]
    assert step["if"] == "steps.instance.outputs.id != '' && !inputs.skip_backup"
    assert "aws s3 sync /opt/ticket-agent/data/handoff/" in step["run"]
    assert "s3://${{ vars.DATA_BUCKET }}/handoff/" in step["run"]
    command = re.search(r"--parameters '([^']+)'", step["run"]).group(1)
    assert "--delete" not in command
    # the waiter fails the step when the command fails; no `|| true` here
    assert "aws ssm wait command-executed" in step["run"]
    assert "|| true" not in step["run"]
    assert "if" not in steps("down")[destroy]  # a failed backup stops it instead


def test_backup_goes_where_the_instance_may_write_and_boot_reads() -> None:
    assert '"${aws_s3_bucket.data.arn}/handoff/*"' in IAM  # instance role PutObject
    boot = (ROOT / "infra" / "boot.sh.tftpl").read_text()
    assert 'aws s3 sync "s3://${data_bucket}/handoff/" data/handoff/' in boot
    assert "APP_DIR=/opt/ticket-agent" in boot


def test_only_this_apps_instance_is_touched() -> None:
    finds = re.findall(r"aws ec2 describe-instances[^)]*", TEXT)
    assert len(finds) == 2
    assert all("Name=tag:App,Values=ticket-agent" in f for f in finds)


def test_variables_come_from_terraform_outputs() -> None:
    outputs = set(
        re.findall(r'output "(\w+)"', (ROOT / "infra" / "outputs.tf").read_text())
    )
    used = set(re.findall(r"vars\.(\w+)", TEXT))
    assert used == {"INFRA_ROLE_ARN", "DATA_BUCKET"}
    for var in used:
        assert var.lower() in outputs, var
