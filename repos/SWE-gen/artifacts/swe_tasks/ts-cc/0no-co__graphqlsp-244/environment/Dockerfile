FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 18 (as specified in CI)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install pnpm 8.6.1 via corepack (as specified in CI)
RUN corepack enable && corepack prepare pnpm@8.6.1 --activate

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/0no-co/GraphQLSP.git src && \
    cd src && \
    (git fetch --depth 1 origin 8f3b6cd012e9d489a949abf776a6c802b349568f || git fetch --depth 1 origin "+refs/pull/244/head:refs/remotes/origin/pr/244") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

ENV CI=true

# Install dependencies
RUN pnpm install --frozen-lockfile

# Build the TypeScript package
RUN pnpm --filter @0no-co/graphqlsp run build

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Rebuild after patching TypeScript source
RUN pnpm --filter @0no-co/graphqlsp run build

RUN rm -rf /app/src/.git

WORKDIR /app/src
