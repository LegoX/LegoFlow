#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "test/suites/api"
cp "/tests/suites/api/test_pack.c" "test/suites/api/test_pack.c"

# Rebuild just the test_pack target after copying the updated test file
cmake --build build --target test_pack

# Run only the test_pack test using ctest
cd build
ctest -V -R "^test_pack$"
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
