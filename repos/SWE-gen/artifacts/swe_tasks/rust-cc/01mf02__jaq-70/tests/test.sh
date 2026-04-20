#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "jaq-core/tests"
cp "/tests/jaq-core/tests/custom.rs" "jaq-core/tests/custom.rs"

# Run the specific integration test file for jaq-core custom filters
cargo test --package jaq-core --test custom -- --nocapture
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
