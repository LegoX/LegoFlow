FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Java 21
RUN apt-get update && apt-get install -y openjdk-21-jdk && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/9tigerio/db2rest.git src && \
    cd src && \
    (git fetch --depth 1 origin e84f684f9d764c23affee0e7c6c8fcfd1bd6b113 || git fetch --depth 1 origin "+refs/pull/152/head:refs/remotes/origin/pr/152") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

ENV CI=true

# Pre-download dependencies and compile
RUN ./mvnw -B dependency:resolve dependency:resolve-plugins -q || true
RUN ./mvnw -B test-compile -DskipTests -q || true

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Recompile after patching
RUN ./mvnw -B test-compile -DskipTests -q || true

RUN rm -rf /app/src/.git

WORKDIR /app/src
