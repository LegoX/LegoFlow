#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "runner"
cp "/tests/runner/config_test.go" "runner/config_test.go"
mkdir -p "runner"
cp "/tests/runner/engine_test.go" "runner/engine_test.go"

go test -v -run "TestRunCommand" ./runner/...
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
