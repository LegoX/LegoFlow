#!/bin/bash

cd /app/src

export RUST_BACKTRACE=1
export CARGO_TERM_COLOR=never

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "node/network/tests"
cp "/tests/node/network/tests/rpc_tests.rs" "node/network/tests/rpc_tests.rs"

# Run only the rpc_tests integration test file for the network package.
# Dependencies are pre-built in the Docker image; only rpc_tests.rs is recompiled.
cargo test -p network --test rpc_tests -- --nocapture
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
