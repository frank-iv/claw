data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  prefix     = "openclaw-review"
}

resource "aws_iam_role" "ec2_review_agent" {
  name = "ec2-review-agent-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "ec2_review_agent" {
  name = "ec2-review-agent-policy"
  role = aws_iam_role.ec2_review_agent.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:UpdateInstanceInformation",
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.review_artifacts.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "${aws_cloudwatch_log_group.review_agent.arn}:*"
      },
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_review_agent" {
  name = "ec2-review-agent-profile"
  role = aws_iam_role.ec2_review_agent.name
}

resource "aws_iam_role" "review_orchestrator" {
  name = "review-orchestrator-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
    }]
  })
}

resource "aws_iam_role_policy" "review_orchestrator" {
  name = "review-orchestrator-policy"
  role = aws_iam_role.review_orchestrator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:RunInstances",
          "ec2:TerminateInstances",
          "ec2:DescribeInstances",
          "ec2:DescribeSpotPriceHistory",
          "ec2:CreateTags",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:SendCommand",
          "ssm:GetCommandInvocation",
          "ssm:DescribeInstanceInformation",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.review_artifacts.arn,
          "${aws_s3_bucket.review_artifacts.arn}/*",
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.ec2_review_agent.arn
      },
    ]
  })
}

resource "aws_security_group" "review_agent" {
  name        = "sg-review-agent"
  description = "Egress-only for EC2 review agent"

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "sg-review-agent"
  }
}

resource "aws_ecr_repository" "review_runner" {
  name                 = "review-runner"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_s3_bucket" "review_artifacts" {
  bucket = "${local.prefix}-artifacts"
}

resource "aws_s3_bucket_lifecycle_configuration" "review_artifacts" {
  bucket = aws_s3_bucket.review_artifacts.id

  rule {
    id     = "expire-artifacts"
    status = "Enabled"

    expiration {
      days = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "review_artifacts" {
  bucket = aws_s3_bucket.review_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudwatch_log_group" "review_agent" {
  name              = "/openclaw/review-agent"
  retention_in_days = 14
}

data "aws_ssm_parameter" "ecs_optimized_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_launch_template" "review_agent" {
  name = "lt-review-agent"

  image_id      = data.aws_ssm_parameter.ecs_optimized_ami.value
  instance_type = "c5.xlarge"

  iam_instance_profile {
    arn = aws_iam_instance_profile.ec2_review_agent.arn
  }

  vpc_security_group_ids = [aws_security_group.review_agent.id]

  instance_market_options {
    market_type = "spot"
    spot_options {
      max_price          = "0.08"
      spot_instance_type = "one-time"
    }
  }

  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }

  user_data = base64encode(<<-USERDATA
    #!/bin/bash
    yum install -y docker amazon-ssm-agent
    systemctl enable --now docker amazon-ssm-agent
    aws ecr get-login-password --region ${local.region} | docker login --username AWS --password-stdin ${local.account_id}.dkr.ecr.${local.region}.amazonaws.com
    docker pull ${aws_ecr_repository.review_runner.repository_url}:latest
    USERDATA
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name    = "review-agent"
      Purpose = "ec2-code-review"
    }
  }
}

output "ecr_repository_url" {
  value = aws_ecr_repository.review_runner.repository_url
}

output "artifacts_bucket" {
  value = aws_s3_bucket.review_artifacts.id
}

output "launch_template_id" {
  value = aws_launch_template.review_agent.id
}

output "orchestrator_role_arn" {
  value = aws_iam_role.review_orchestrator.arn
}
