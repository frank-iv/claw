from __future__ import annotations

import pytest

from ec2_stages import (
    STAGE_NAMES,
    StageCommand,
    build_all_stage_commands,
    build_clone_command,
    build_stage_command,
)

ECR_IMAGE = "123456.dkr.ecr.us-east-1.amazonaws.com/review:latest"
REPO_PATH = "https://github.com/org/repo.git"
BRANCH = "feature/pr-42"
BUCKET = "artifacts-bucket"
INSTANCE_ID = "i-0abc123"


def test_build_stage_command_tests_returns_stage_command() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert isinstance(result, StageCommand)
    assert result.stage == "tests"


def test_build_stage_command_tests_docker_command_contains_image() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert ECR_IMAGE in result.docker_command


def test_build_stage_command_tests_docker_command_contains_stage_arg() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert "tests /workspace" in result.docker_command


def test_build_stage_command_lint_uses_lint_stage() -> None:
    result = build_stage_command("lint", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert result.stage == "lint"
    assert "lint /workspace" in result.docker_command


def test_build_stage_command_security_uses_security_stage() -> None:
    result = build_stage_command("security", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert result.stage == "security"
    assert "security /workspace" in result.docker_command


def test_build_stage_command_includes_s3_upload() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert f"s3://{BUCKET}/{INSTANCE_ID}/" in result.docker_command


def test_build_stage_command_chains_docker_and_upload() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert " && " in result.docker_command


def test_build_stage_command_mounts_repo_volume() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert "-v /tmp/repo:/workspace" in result.docker_command


def test_build_stage_command_mounts_output_volume() -> None:
    result = build_stage_command("tests", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)
    assert "-v /tmp/output:/output" in result.docker_command


def test_build_stage_command_invalid_stage_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown stage: deploy"):
        build_stage_command("deploy", ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID)


def test_build_clone_command_includes_branch() -> None:
    result = build_clone_command("https://github.com/org/repo.git", "main")
    assert "--branch main" in result


def test_build_clone_command_shallow_clone() -> None:
    result = build_clone_command("https://github.com/org/repo.git", "main")
    assert "--depth 1" in result


def test_build_clone_command_clones_to_tmp_repo() -> None:
    result = build_clone_command("https://github.com/org/repo.git", "main")
    assert result.endswith("/tmp/repo")


def test_build_clone_command_includes_repo_url() -> None:
    result = build_clone_command("https://github.com/org/repo.git", "main")
    assert "https://github.com/org/repo.git" in result


def test_build_all_stage_commands_returns_all_stages() -> None:
    results = build_all_stage_commands(
        ["tests", "lint", "security"], ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID
    )
    assert len(results) == 3
    assert [r.stage for r in results] == ["tests", "lint", "security"]


def test_build_all_stage_commands_each_element_is_stage_command() -> None:
    results = build_all_stage_commands(
        ["tests", "lint"], ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID
    )
    assert all(isinstance(r, StageCommand) for r in results)


def test_build_all_stage_commands_empty_list() -> None:
    results = build_all_stage_commands(
        [], ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID
    )
    assert results == []


def test_build_all_stage_commands_invalid_stage_raises() -> None:
    with pytest.raises(ValueError, match="Unknown stage: bad"):
        build_all_stage_commands(
            ["tests", "bad"], ECR_IMAGE, REPO_PATH, BRANCH, BUCKET, INSTANCE_ID
        )


def test_stage_names_contains_expected_values() -> None:
    assert STAGE_NAMES == {"tests", "lint", "security"}
