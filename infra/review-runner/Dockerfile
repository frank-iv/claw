FROM python:3.12-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    ruff==0.4.4 \
    bandit==1.7.8 \
    semgrep==1.72.0 \
    pytest==8.2.1 \
    pytest-json-report==1.5.0

RUN mkdir -p /output

WORKDIR /workspace

COPY run_stage.sh /usr/local/bin/run_stage.sh
RUN chmod +x /usr/local/bin/run_stage.sh

ENTRYPOINT ["/usr/local/bin/run_stage.sh"]
