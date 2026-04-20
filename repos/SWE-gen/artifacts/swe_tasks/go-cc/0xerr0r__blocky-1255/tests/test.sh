#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "e2e"
cp "/tests/e2e/basic_test.go" "e2e/basic_test.go"

# Build the blocky Docker image required by testcontainers e2e tests
docker buildx build \
    --build-arg VERSION=blocky-e2e \
    --network=host \
    -o type=docker \
    -t blocky-e2e \
    .

# Run only the Logging tests from basic_test.go
go run github.com/onsi/ginkgo/v2/ginkgo \
    --label-filter="e2e" \
    --focus="Logging" \
    --timeout 15m \
    e2e

test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
