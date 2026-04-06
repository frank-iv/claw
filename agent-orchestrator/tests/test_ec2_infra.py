from __future__ import annotations

import time
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from ec2_infra import (
    InstanceArtifacts,
    SSMCommandResult,
    SpotInstanceInfo,
    collect_artifacts,
    request_spot_instance,
    ssm_run,
    terminate_instance,
    wait_for_ssm_ready,
)


class FakeClient:
    def __init__(self, mock: AsyncMock) -> None:
        self._mock = mock

    async def __aenter__(self) -> AsyncMock:
        return self._mock

    async def __aexit__(self, *args: object) -> None:
        pass


class FakeSession:
    def __init__(self, clients: dict[str, AsyncMock]) -> None:
        self._clients = clients

    def create_client(self, service: str, **kwargs: object) -> FakeClient:
        return FakeClient(self._clients[service])


def make_session(clients: dict[str, AsyncMock]) -> FakeSession:
    return FakeSession(clients)


@pytest.mark.asyncio
async def test_request_spot_instance_returns_spot_instance_info() -> None:
    ec2_mock = AsyncMock()
    ec2_mock.run_instances.return_value = {
        "Instances": [
            {
                "InstanceId": "i-abc123",
                "SpotInstanceRequestId": "sir-xyz",
            }
        ]
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ec2": ec2_mock})):
        result = await request_spot_instance("lt-template123")

    assert isinstance(result, SpotInstanceInfo)
    assert result.instance_id == "i-abc123"
    assert result.spot_request_id == "sir-xyz"
    assert result.launch_time_ms > 0


@pytest.mark.asyncio
async def test_request_spot_instance_calls_run_instances_with_template() -> None:
    ec2_mock = AsyncMock()
    ec2_mock.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-1", "SpotInstanceRequestId": "sir-1"}]
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ec2": ec2_mock})):
        await request_spot_instance("lt-abc", region="us-west-2")

    ec2_mock.run_instances.assert_called_once()
    call_kwargs = ec2_mock.run_instances.call_args.kwargs
    assert call_kwargs["LaunchTemplate"]["LaunchTemplateId"] == "lt-abc"
    assert call_kwargs["InstanceMarketOptions"]["MarketType"] == "spot"


@pytest.mark.asyncio
async def test_request_spot_instance_missing_spot_request_id_defaults_empty() -> None:
    ec2_mock = AsyncMock()
    ec2_mock.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-1"}]
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ec2": ec2_mock})):
        result = await request_spot_instance("lt-abc")

    assert result.spot_request_id == ""


@pytest.mark.asyncio
async def test_wait_for_ssm_ready_returns_when_online() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.describe_instance_information.return_value = {
        "InstanceInformationList": [{"PingStatus": "Online"}]
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        await wait_for_ssm_ready("i-abc", timeout_seconds=10, poll_interval=0.01)

    ssm_mock.describe_instance_information.assert_called_once()


@pytest.mark.asyncio
async def test_wait_for_ssm_ready_polls_until_online() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.describe_instance_information.side_effect = [
        {"InstanceInformationList": []},
        {"InstanceInformationList": [{"PingStatus": "Inactive"}]},
        {"InstanceInformationList": [{"PingStatus": "Online"}]},
    ]

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        await wait_for_ssm_ready("i-abc", timeout_seconds=30, poll_interval=0.01)

    assert ssm_mock.describe_instance_information.call_count == 3


@pytest.mark.asyncio
async def test_wait_for_ssm_ready_timeout_raises() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.describe_instance_information.return_value = {
        "InstanceInformationList": []
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        with pytest.raises(TimeoutError, match="not ready within"):
            await wait_for_ssm_ready("i-abc", timeout_seconds=0.05, poll_interval=0.01)


@pytest.mark.asyncio
async def test_ssm_run_returns_command_result_on_success() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.send_command.return_value = {
        "Command": {"CommandId": "cmd-123"}
    }
    ssm_mock.get_command_invocation.return_value = {
        "Status": "Success",
        "StandardOutputContent": "hello",
        "StandardErrorContent": "",
        "ResponseCode": 0,
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        result = await ssm_run("i-abc", "echo hello", poll_interval=0.01)

    assert isinstance(result, SSMCommandResult)
    assert result.command_id == "cmd-123"
    assert result.status == "Success"
    assert result.stdout == "hello"
    assert result.stderr == ""
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_ssm_run_returns_on_failed_status() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.send_command.return_value = {
        "Command": {"CommandId": "cmd-456"}
    }
    ssm_mock.get_command_invocation.return_value = {
        "Status": "Failed",
        "StandardOutputContent": "",
        "StandardErrorContent": "error occurred",
        "ResponseCode": 1,
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        result = await ssm_run("i-abc", "bad-cmd", poll_interval=0.01)

    assert result.status == "Failed"
    assert result.stderr == "error occurred"
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_ssm_run_polls_until_terminal_status() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.send_command.return_value = {
        "Command": {"CommandId": "cmd-789"}
    }
    ssm_mock.get_command_invocation.side_effect = [
        {"Status": "InProgress"},
        {"Status": "InProgress"},
        {
            "Status": "Success",
            "StandardOutputContent": "done",
            "StandardErrorContent": "",
            "ResponseCode": 0,
        },
    ]

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        result = await ssm_run("i-abc", "slow-cmd", poll_interval=0.01)

    assert result.status == "Success"
    assert ssm_mock.get_command_invocation.call_count == 3


@pytest.mark.asyncio
async def test_ssm_run_sends_correct_command_params() -> None:
    ssm_mock = AsyncMock()
    ssm_mock.send_command.return_value = {
        "Command": {"CommandId": "cmd-1"}
    }
    ssm_mock.get_command_invocation.return_value = {
        "Status": "Success",
        "StandardOutputContent": "",
        "StandardErrorContent": "",
        "ResponseCode": 0,
    }

    with patch("ec2_infra._create_session", return_value=make_session({"ssm": ssm_mock})):
        await ssm_run("i-abc", "ls -la", poll_interval=0.01, timeout_seconds=120)

    ssm_mock.send_command.assert_called_once_with(
        InstanceIds=["i-abc"],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["ls -la"]},
        TimeoutSeconds=120,
    )


@pytest.mark.asyncio
async def test_collect_artifacts_returns_instance_artifacts() -> None:
    s3_mock = AsyncMock()
    s3_mock.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "prefix/i-abc/results.json"},
        ]
    }
    body_mock = AsyncMock()
    body_mock.read.return_value = b'{"passed": true}'
    s3_mock.get_object.return_value = {"Body": body_mock}

    with patch("ec2_infra._create_session", return_value=make_session({"s3": s3_mock})):
        result = await collect_artifacts("i-abc", "my-bucket", "prefix")

    assert isinstance(result, InstanceArtifacts)
    assert result.instance_id == "i-abc"
    assert result.artifacts["results.json"] == {"passed": True}


@pytest.mark.asyncio
async def test_collect_artifacts_non_json_stored_as_string() -> None:
    s3_mock = AsyncMock()
    s3_mock.list_objects_v2.return_value = {
        "Contents": [
            {"Key": "prefix/i-abc/output.txt"},
        ]
    }
    body_mock = AsyncMock()
    body_mock.read.return_value = b"plain text output"
    s3_mock.get_object.return_value = {"Body": body_mock}

    with patch("ec2_infra._create_session", return_value=make_session({"s3": s3_mock})):
        result = await collect_artifacts("i-abc", "my-bucket", "prefix")

    assert result.artifacts["output.txt"] == "plain text output"


@pytest.mark.asyncio
async def test_collect_artifacts_empty_bucket() -> None:
    s3_mock = AsyncMock()
    s3_mock.list_objects_v2.return_value = {}

    with patch("ec2_infra._create_session", return_value=make_session({"s3": s3_mock})):
        result = await collect_artifacts("i-abc", "my-bucket", "prefix")

    assert result.artifacts == {}


@pytest.mark.asyncio
async def test_terminate_instance_calls_ec2_terminate() -> None:
    ec2_mock = AsyncMock()
    ec2_mock.terminate_instances.return_value = {}

    with patch("ec2_infra._create_session", return_value=make_session({"ec2": ec2_mock})):
        await terminate_instance("i-abc123")

    ec2_mock.terminate_instances.assert_called_once_with(InstanceIds=["i-abc123"])


@pytest.mark.asyncio
async def test_terminate_instance_uses_specified_region() -> None:
    ec2_mock = AsyncMock()
    ec2_mock.terminate_instances.return_value = {}

    session = make_session({"ec2": ec2_mock})
    original_create_client = session.create_client
    called_with_region: list[str] = []

    def tracking_create_client(service: str, **kwargs: object) -> FakeClient:
        if "region_name" in kwargs:
            called_with_region.append(str(kwargs["region_name"]))
        return original_create_client(service, **kwargs)

    session.create_client = tracking_create_client  # type: ignore[assignment]

    with patch("ec2_infra._create_session", return_value=session):
        await terminate_instance("i-abc", region="eu-west-1")

    assert called_with_region == ["eu-west-1"]
