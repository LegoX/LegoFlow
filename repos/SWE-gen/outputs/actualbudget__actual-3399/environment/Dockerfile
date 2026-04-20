FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 18 (matches .nvmrc v18.16.0)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Enable corepack for yarn (packageManager: yarn@4.3.1)
RUN corepack enable

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/actualbudget/actual.git src && \
    cd src && \
    (git fetch --depth 1 origin bf42f57c1d660646d74d37d3033b6cf3c9607a48 || git fetch --depth 1 origin "+refs/pull/3399/head:refs/remotes/origin/pr/3399") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

ENV CI=true

# Install dependencies (python3 is needed for better-sqlite3 native compilation)
RUN yarn install --frozen-lockfile

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

RUN rm -rf /app/src/.git

WORKDIR /app/src
