from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageCommand:
    stage: str
    docker_command: str


STAGE_NAMES = frozenset({"tests", "lint", "security"})


def build_stage_command(
    stage: str,
    ecr_image_uri: str,
    repo_path: str,
    branch: str,
    artifacts_bucket: str,
    instance_id: str,
) -> StageCommand:
    if stage not in STAGE_NAMES:
        raise ValueError(f"Unknown stage: {stage}. Valid: {STAGE_NAMES}")

    upload_cmd = (
        f"aws s3 cp /tmp/output/ s3://{artifacts_bucket}/{instance_id}/ "
        f"--recursive --quiet"
    )

    docker_cmd = (
        f"docker run --rm "
        f"-v /tmp/repo:/workspace "
        f"-v /tmp/output:/output "
        f"{ecr_image_uri} "
        f"{stage} /workspace"
    )

    full_command = f"{docker_cmd} && {upload_cmd}"

    return StageCommand(stage=stage, docker_command=full_command)


def build_clone_command(repo_url: str, branch: str) -> str:
    return f"git clone --depth 1 --branch {branch} {repo_url} /tmp/repo"


def build_all_stage_commands(
    stages: list[str],
    ecr_image_uri: str,
    repo_path: str,
    branch: str,
    artifacts_bucket: str,
    instance_id: str,
) -> list[StageCommand]:
    return [
        build_stage_command(
            stage=stage,
            ecr_image_uri=ecr_image_uri,
            repo_path=repo_path,
            branch=branch,
            artifacts_bucket=artifacts_bucket,
            instance_id=instance_id,
        )
        for stage in stages
    ]
