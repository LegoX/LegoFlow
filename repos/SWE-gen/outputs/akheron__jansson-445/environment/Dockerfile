FROM ubuntu:24.04

# Base system packages (common to all languages)
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/akheron/jansson.git src && \
    cd src && \
    (git fetch --depth 1 origin e262ea5fcd789d20d5d20d5d6d9c7ec06e3c00fd || git fetch --depth 1 origin "+refs/pull/445/head:refs/remotes/origin/pr/445") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Build the project with CMake after applying bug.patch
RUN cmake -B build -DJANSSON_BUILD_DOCS=OFF && cmake --build build

RUN rm -rf /app/src/.git

WORKDIR /app/src
