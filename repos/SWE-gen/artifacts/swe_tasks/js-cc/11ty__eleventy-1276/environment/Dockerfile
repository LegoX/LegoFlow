FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20.x
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/11ty/eleventy.git src && \
    cd src && \
    (git fetch --depth 1 origin 79f7af1a5931b052e91c6c8945cd35d351c0547d || git fetch --depth 1 origin "+refs/pull/1276/head:refs/remotes/origin/pr/1276") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# Set environment variables
ENV CI=true
ENV NODE_ENV=test

# Install dependencies
RUN npm ci

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Remove git history (prevents agent from seeing the fix via git log/show),
# then re-init a fresh repo so that `git diff` can track agent's changes.
RUN cd /app/src && rm -rf .git && git init && git config user.email "build@swegen" && git config user.name "swegen" && git add -A && git commit -m "baseline" --allow-empty

WORKDIR /app/src
