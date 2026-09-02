# Cloud Agent base image for JanusGate.
# System-level, slow-changing dependencies live here; repository dependency
# installation happens in .cursor/install.sh, and services start in
# .cursor/start.sh. See https://cursor.com/docs/cloud-agent/setup
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# Base toolchain + backend build/runtime system libraries (python-ldap needs
# libldap2-dev/libsasl2-dev, asyncpg build needs libpq-dev) + local PostgreSQL 16
# and Redis servers used for the end-to-end development flow.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
        sudo \
        procps \
        locales \
        python3 \
        python3-venv \
        python3-dev \
        build-essential \
        libldap2-dev \
        libsasl2-dev \
        libpq-dev \
        postgresql \
        postgresql-contrib \
        redis-server \
    && rm -rf /var/lib/apt/lists/*

# Node.js 24 (matches CI) for the React/Vite frontend.
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# The ubuntu:24.04 image ships a default non-root "ubuntu" user (uid 1000).
# Give it passwordless sudo so start-up can manage the local PostgreSQL/Redis.
RUN usermod -aG sudo ubuntu \
    && echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-ubuntu \
    && chmod 0440 /etc/sudoers.d/90-ubuntu

USER ubuntu
WORKDIR /home/ubuntu

# uv is the backend package manager pinned by backend/uv.lock.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/home/ubuntu/.local/bin:${PATH}"
