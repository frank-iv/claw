# EC2 Code Review Agent - Architecture

## Overview

A new `ExecutionMode.EC2` that extends the existing orchestrator. Instead of running Claude locally (CLI/SDK), it provisions a spot EC2 instance, runs review tasks in Docker containers via SSM, collects structured JSON results, and terminates the instance.

## Data Flow

```
dispatcher.py --agent ec2-review
       │
       ▼
orchestrator.py (dispatch → EC2 mode)
       │
       ▼
ec2_runner.py
  1. request_spot_instance()     → instance_id
  2. wait_for_ssm_ready()        → SSM agent online
  3. ssm_run("git clone + docker pull")
  4. for stage in [tests, lint, security]:
         ssm_run(docker_command[stage]) → StageResult
  5. collect_artifacts() from S3  → structured JSON
  6. terminate_instance()
       │
       ▼
EC2RunResult (structured JSON back to orchestrator)
```

## New Files

```
agent-orchestrator/
  ec2_runner.py          # lifecycle orchestration
  ec2_infra.py           # boto3/aiobotocore wrappers (SSM, EC2, S3)
  ec2_stages.py          # stage command definitions
infra/
  review-runner/
    Dockerfile           # review toolchain image (ruff, bandit, semgrep, pytest)
  terraform/
    ec2_review.tf        # all AWS resources
```

## AWS Resources

| Resource | Purpose |
|----------|---------|
| IAM Role `ec2-review-agent-role` | EC2 instance role: ECR pull, SSM, S3 artifact write, CloudWatch |
| IAM Role `review-orchestrator-role` | Orchestrator: EC2 RunInstances/Terminate, SSM SendCommand, S3 read |
| Security Group `sg-review-agent` | Egress-only (HTTPS). No inbound — SSM eliminates SSH |
| ECR Repo `review-runner` | Pre-built Docker image with all review tools |
| S3 Bucket `openclaw-review-artifacts` | Stage result JSON files |
| Launch Template `lt-review-agent` | Spot config, AMI, IAM profile, user-data |

## Key Decisions

- **SSM over SSH**: No key management, no inbound ports
- **Spot instances**: c5.xlarge ~$0.03-0.07/hr, review takes 2-5 min, cost per review <$0.01
- **Docker on EC2 (not ECS)**: Simpler — one instance, multiple docker run via SSM
- **Pre-baked AMI + ECR**: AMI has Docker+SSM, toolchain in ECR for independent updates
- **S3 for large artifacts**: SSM output capped at 24KB, large results go through S3

## Example Usage

```bash
python dispatcher.py run \
  "Review PR #42" \
  --agent ec2-review \
  --repo https://github.com/org/repo.git \
  --branch feat/new-api \
  --stages tests,lint,security
```

## Output Format

```json
{
  "agent": "ec2-review",
  "instance_id": "i-0abc123def456",
  "spot_price_usd": 0.034,
  "duration_ms": 187000,
  "results": {
    "tests": {"exit_code": 0, "duration_ms": 95000, "artifacts": {"passed": 142, "failed": 0}},
    "lint": {"exit_code": 0, "duration_ms": 12000, "artifacts": {"violations": 3}},
    "security": {"exit_code": 0, "duration_ms": 45000, "artifacts": {"bandit": {"high": 0}, "semgrep": {"findings": 0}}}
  }
}
```
