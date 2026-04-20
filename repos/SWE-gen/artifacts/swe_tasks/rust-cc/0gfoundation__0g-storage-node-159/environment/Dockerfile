FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    pkg-config \
    libssl-dev \
    clang \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# Install Rust 1.75.0 (as specified in rust-toolchain)
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.75.0 --no-modify-path

ENV CARGO_TERM_COLOR=never

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/0gfoundation/0g-storage-node.git src && \
    cd src && \
    (git fetch --depth 1 origin 2ab7f673e445aa6d280216c5e2384a6849b636b4 || git fetch --depth 1 origin "+refs/pull/159/head:refs/remotes/origin/pr/159") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# Fetch and build test dependencies for the network package (with the fixed code)
RUN cargo fetch
RUN cargo build -p network --tests 2>&1 | tail -5 || cargo build -p network 2>&1 | tail -5

# Reset tracked files so bug.patch applies cleanly
RUN git reset --hard

# Apply bug.patch to revert to buggy state
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Rebuild after patching so the binary reflects the buggy state
RUN cargo build -p network --tests 2>&1 | tail -5 || true

RUN rm -rf /app/src/.git

WORKDIR /app/src
