# Use the pre-built C++ dependencies image (Ubuntu 24.04 with Trilinos, MPI, HDF5, etc.)
FROM ghcr.io/4c-multiphysics/4c-dependencies-ubuntu24.04:bbac1cd9

USER root
# git, patch, cmake, ninja are already in the pre-built image
RUN apt-get update && apt-get install -y \
    git \
    patch \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone repo at HEAD commit (with fix applied)
RUN git clone https://github.com/4C-multiphysics/4C.git src && \
    cd src && \
    (git fetch --depth 1 origin fb4a718cb5e274a2f832f27a0ba96b2de5cf459e || git fetch --depth 1 origin "+refs/pull/102/head:refs/remotes/origin/pr/102") && \
    git checkout --detach FETCH_HEAD && \
    git submodule update --init --recursive

WORKDIR /app/src

# Required for running MPI as root
ENV OMPI_ALLOW_RUN_AS_ROOT=1
ENV OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1

# If install/build steps touched tracked files, reset them so bug.patch applies cleanly,
RUN git reset --hard

# Apply bug.patch to revert to buggy state (BASE)
COPY bug.patch /tmp/bug.patch
RUN patch -p1 < /tmp/bug.patch && rm /tmp/bug.patch

# Configure and build only the relevant test targets
RUN mkdir -p /app/build && \
    cd /app/build && \
    cmake /app/src --fresh --preset=docker

RUN cmake --build /app/build --target unittests_mat unittests_mat_vplast -- -j $(nproc)

RUN rm -rf /app/src/.git

WORKDIR /app/src
