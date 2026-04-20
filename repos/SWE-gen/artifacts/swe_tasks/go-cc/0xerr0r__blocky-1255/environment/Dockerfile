FROM ubuntu:24.04

# Base system packages
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Go 1.21
RUN curl -fsSL https://go.dev/dl/go1.21.6.linux-amd64.tar.gz | tar -C /usr/local -xzf -
ENV PATH="/usr/local/go/bin:${PATH}"

# Install Docker CLI binary (needed to build blocky-e2e image for testcontainers e2e tests)
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.0.3.tgz | \
    tar -xz -C /usr/local/bin --strip-components=1 docker/docker

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/0xERR0R/blocky.git src && \
    cd src && \
    (git fetch --depth 1 origin 0c98d394640772357297d0c260f87456977da420 || git fetch --depth 1 origin "+refs/pull/1255/head:refs/remotes/origin/pr/1255") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

ENV CI=true
ENV GOPATH=/root/go

# Download Go module dependencies
RUN go mod download

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

RUN rm -rf /app/src/.git

WORKDIR /app/src
