#!/bin/bash

cd /app/src

export CI=true
export NODE_ENV=test

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "test"
cp "/tests/UrlTest.js" "test/UrlTest.js"

# Install dependencies if needed
npm ci 2>/dev/null || true

npx ava test/UrlTest.js
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"