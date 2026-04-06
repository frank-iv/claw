from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import aiobotocore.session


@dataclass
class SpotInstanceInfo:
    instance_id: str
    spot_request_id: str
    launch_time_ms: int


@dataclass
class SSMCommandResult:
    command_id: str
    status: str
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class S3Artifact:
    key: str
    content: str


@dataclass
class InstanceArtifacts:
    instance_id: str
    artifacts: dict[str, object] = field(default_factory=dict)


def _create_session() -> aiobotocore.session.AioSession:
    return aiobotocore.session.get_session()


async def request_spot_instance(
    launch_template_id: str,
    region: str = "us-east-1",
) -> SpotInstanceInfo:
    session = _create_session()
    async with session.create_client("ec2", region_name=region) as ec2:
        response = await ec2.run_instances(
            LaunchTemplate={"LaunchTemplateId": launch_template_id},
            MinCount=1,
            MaxCount=1,
            InstanceMarketOptions={
                "MarketType": "spot",
                "SpotOptions": {
                    "SpotInstanceType": "one-time",
                },
            },
        )
        instance = response["Instances"][0]
        return SpotInstanceInfo(
            instance_id=instance["InstanceId"],
            spot_request_id=instance.get("SpotInstanceRequestId", ""),
            launch_time_ms=int(time.time() * 1000),
        )


async def wait_for_ssm_ready(
    instance_id: str,
    region: str = "us-east-1",
    timeout_seconds: int = 300,
    poll_interval: float = 5.0,
) -> None:
    session = _create_session()
    deadline = time.monotonic() + timeout_seconds
    async with session.create_client("ssm", region_name=region) as ssm:
        while time.monotonic() < deadline:
            response = await ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}],
            )
            info_list = response.get("InstanceInformationList", [])
            if info_list and info_list[0].get("PingStatus") == "Online":
                return
            await asyncio.sleep(poll_interval)
    raise TimeoutError(f"SSM agent on {instance_id} not ready within {timeout_seconds}s")


async def ssm_run(
    instance_id: str,
    command: str,
    region: str = "us-east-1",
    timeout_seconds: int = 600,
    poll_interval: float = 3.0,
) -> SSMCommandResult:
    session = _create_session()
    async with session.create_client("ssm", region_name=region) as ssm:
        send_response = await ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
            TimeoutSeconds=timeout_seconds,
        )
        command_id = send_response["Command"]["CommandId"]

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            invocation = await ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
            status = invocation["Status"]
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                return SSMCommandResult(
                    command_id=command_id,
                    status=status,
                    stdout=invocation.get("StandardOutputContent", ""),
                    stderr=invocation.get("StandardErrorContent", ""),
                    exit_code=invocation.get("ResponseCode", -1),
                )

    raise TimeoutError(f"SSM command {command_id} did not complete within {timeout_seconds}s")


async def collect_artifacts(
    instance_id: str,
    bucket: str,
    prefix: str,
    region: str = "us-east-1",
) -> InstanceArtifacts:
    session = _create_session()
    result = InstanceArtifacts(instance_id=instance_id)
    async with session.create_client("s3", region_name=region) as s3:
        list_response = await s3.list_objects_v2(
            Bucket=bucket,
            Prefix=f"{prefix}/{instance_id}/",
        )
        for obj in list_response.get("Contents", []):
            key = obj["Key"]
            get_response = await s3.get_object(Bucket=bucket, Key=key)
            body = await get_response["Body"].read()
            content = body.decode()
            filename = key.rsplit("/", 1)[-1]
            try:
                result.artifacts[filename] = json.loads(content)
            except json.JSONDecodeError:
                result.artifacts[filename] = content
    return result


async def terminate_instance(
    instance_id: str,
    region: str = "us-east-1",
) -> None:
    session = _create_session()
    async with session.create_client("ec2", region_name=region) as ec2:
        await ec2.terminate_instances(InstanceIds=[instance_id])
