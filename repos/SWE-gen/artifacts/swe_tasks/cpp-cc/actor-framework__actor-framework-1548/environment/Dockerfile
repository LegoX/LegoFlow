FROM ubuntu:24.04

# Base system packages
RUN apt-get update && apt-get install -y \
    git \
    curl \
    ca-certificates \
    patch \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install C++ build dependencies (matching the project's own CI config)
RUN apt-get update && apt-get install -y \
    cmake \
    g++ \
    libssl-dev \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/actor-framework/actor-framework.git src && \
    cd src && \
    (git fetch --depth 1 origin c3d211bb08d3b51ea1300351efc5185d063d216d || git fetch --depth 1 origin "+refs/pull/1548/head:refs/remotes/origin/pr/1548") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# Build the project with CMake - only libcaf_core and its tests (skip IO/net/openssl to reduce build time)
RUN cmake -S . -B /app/build \
    -DCMAKE_BUILD_TYPE=Release \
    -DCAF_ENABLE_TESTING=ON \
    -DCAF_ENABLE_IO_MODULE=OFF \
    -DCAF_ENABLE_NET_MODULE=OFF \
    -DCAF_ENABLE_OPENSSL_MODULE=OFF \
    -DCAF_ENABLE_EXAMPLES=OFF \
    -DCAF_ENABLE_TOOLS=OFF && \
    cmake --build /app/build --target caf-core-legacy-test -j$(nproc)

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Rebuild the test binary after applying bug.patch
RUN cmake --build /app/build --target caf-core-legacy-test -j$(nproc)

RUN rm -rf /app/src/.git

WORKDIR /app/src
