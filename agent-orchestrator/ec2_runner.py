from __future__ import annotations

import time
from dataclasses import dataclass, field

from agents import AgentDefinition
from ec2_infra import (
    InstanceArtifacts,
    SSMCommandResult,
    collect_artifacts,
    request_spot_instance,
    ssm_run,
    terminate_instance,
    wait_for_ssm_ready,
)
from ec2_stages import build_all_stage_commands, build_clone_command


ARTIFACTS_BUCKET = "openclaw-review-artifacts"
ECR_IMAGE_URI = "openclaw/review-runner:latest"
LAUNCH_TEMPLATE_ID = "lt-review-agent"


class SpotInterruptionError(Exception):
    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        super().__init__(f"Spot instance {instance_id} was interrupted")


@dataclass
class StageResult:
    stage: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    artifacts: dict[str, object] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class EC2RunResult:
    agent_name: str
    instance_id: str
    results: dict[str, StageResult]
    duration_ms: int
    is_error: bool
    spot_price_usd: float

    @property
    def all_stages_passed(self) -> bool:
        return all(r.succeeded for r in self.results.values())


def _stage_result_from_ssm(
    stage: str,
    ssm_result: SSMCommandResult,
    start_ms: int,
) -> StageResult:
    return StageResult(
        stage=stage,
        exit_code=ssm_result.exit_code,
        stdout=ssm_result.stdout,
        stderr=ssm_result.stderr,
        duration_ms=_now_ms() - start_ms,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _merge_artifacts(
    results: dict[str, StageResult],
    instance_artifacts: InstanceArtifacts,
) -> None:
    for filename, content in instance_artifacts.artifacts.items():
        for stage_name, stage_result in results.items():
            if filename.startswith(stage_name):
                stage_result.artifacts[filename] = content
                break


async def run_ec2_review(
    agent: AgentDefinition,
    repo_url: str,
    branch: str,
    stages: list[str],
    instance_type: str,
    region: str,
) -> EC2RunResult:
    run_start = _now_ms()
    instance_id = ""
    results: dict[str, StageResult] = {}
    is_error = False

    try:
        spot_info = await request_spot_instance(LAUNCH_TEMPLATE_ID, region=region)
        instance_id = spot_info.instance_id

        await wait_for_ssm_ready(instance_id, region=region)

        clone_cmd = build_clone_command(repo_url, branch)
        clone_result = await ssm_run(instance_id, clone_cmd, region=region)
        if clone_result.exit_code != 0:
            raise RuntimeError(
                f"Clone failed (exit {clone_result.exit_code}): {clone_result.stderr}"
            )

        stage_commands = build_all_stage_commands(
            stages=stages,
            ecr_image_uri=ECR_IMAGE_URI,
            repo_path="/tmp/repo",
            branch=branch,
            artifacts_bucket=ARTIFACTS_BUCKET,
            instance_id=instance_id,
        )

        for stage_cmd in stage_commands:
            stage_start = _now_ms()
            ssm_result = await ssm_run(
                instance_id,
                stage_cmd.docker_command,
                region=region,
            )

            if ssm_result.status == "Failed" and "spot interruption" in ssm_result.stderr.lower():
                raise SpotInterruptionError(instance_id)

            results[stage_cmd.stage] = _stage_result_from_ssm(
                stage_cmd.stage, ssm_result, stage_start
            )

        artifacts = await collect_artifacts(
            instance_id, ARTIFACTS_BUCKET, "artifacts", region=region
        )
        _merge_artifacts(results, artifacts)

    except SpotInterruptionError:
        is_error = True
        raise
    except Exception:
        is_error = True
    finally:
        if instance_id:
            await terminate_instance(instance_id, region=region)

    return EC2RunResult(
        agent_name=agent.name,
        instance_id=instance_id,
        results=results,
        duration_ms=_now_ms() - run_start,
        is_error=is_error,
        spot_price_usd=0.0,
    )
