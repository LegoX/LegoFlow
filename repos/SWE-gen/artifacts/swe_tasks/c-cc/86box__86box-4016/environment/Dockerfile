FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# TODO: Install language runtime
# Analyze the repo to determine what's needed. Examples:
#   Python: apt-get install python3 python3-pip python3-venv python3-dev
#   Node.js: curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs
#   Go: Download from golang.org/dl or use apt
#   Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
#   Ruby: apt-get install ruby ruby-dev
#   Java: apt-get install openjdk-17-jdk
# Check .nvmrc, .python-version, .ruby-version, go.mod, rust-toolchain.toml, etc.

# TODO: Install additional system packages if needed
# Check CI config (.github/workflows/*.yml) for hints about required packages
# Examples: python3-dev, libssl-dev, pkg-config, cmake, etc.

# TODO: Set up package manager if needed
# For Python: PREFER uv (much faster than pip)
#   curl -LsSf https://astral.sh/uv/install.sh | sh && mv /root/.local/bin/uv /usr/local/bin/uv
# For Node.js: corepack enable (for yarn/pnpm) or npm is built-in
# For Ruby: gem install bundler

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/86Box/86Box.git src && \
    cd src && \
    (git fetch --depth 1 origin 02bbf490dbfb559a0ede151210b1a39746658d8f || git fetch --depth 1 origin "+refs/pull/4016/head:refs/remotes/origin/pr/4016") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# TODO: Set environment variables if needed
# Check CI config and README for required env vars
# Examples: CI=true, NODE_ENV=test, CARGO_TERM_COLOR=never

# TODO: Install dependencies
# For Python: PREFER uv (much faster). Create venv and install:
#   uv venv /opt/venv
#   uv pip install --python /opt/venv/bin/python -e ".[dev,test]"
#   # Or: uv pip install --python /opt/venv/bin/python -r requirements.txt
#   # Then add to PATH: ENV PATH="/opt/venv/bin:${PATH}"
# For Node.js: npm ci, yarn install --frozen-lockfile, pnpm install --frozen-lockfile
# For Go: go mod download
# For Rust: cargo fetch
# For Ruby: bundle install
# For Java: mvn dependency:resolve or gradle dependencies

# TODO: Build if needed (check if it's a compiled language or has build step)
# Examples:
#   TypeScript: npm run build, tsc
#   Rust: cargo build
#   Go: go build ./...
#   Java: mvn compile, gradle build

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# TODO: Rebuild after applying bug.patch if needed
# For compiled languages (TypeScript, Rust, Go, Java), you MUST rebuild after patching

RUN rm -rf /app/src/.git

WORKDIR /app/src
