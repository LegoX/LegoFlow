#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "test/e2e/fixture-project-tada/fixtures"
cp "/tests/e2e/fixture-project-tada/fixtures/fragment.ts" "test/e2e/fixture-project-tada/fixtures/fragment.ts"
mkdir -p "test/e2e/fixture-project/fixtures"
cp "/tests/e2e/fixture-project/fixtures/simple.ts" "test/e2e/fixture-project/fixtures/simple.ts"
mkdir -p "test/e2e"
cp "/tests/e2e/graphqlsp.test.ts" "test/e2e/graphqlsp.test.ts"
mkdir -p "test/e2e"
cp "/tests/e2e/tada.test.ts" "test/e2e/tada.test.ts"

pnpm exec vitest run --single-thread "test/e2e/graphqlsp.test.ts|test/e2e/tada.test.ts"
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
