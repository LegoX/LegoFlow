#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "libcaf_core/tests/legacy/mixin"
cp "/tests/libcaf_core/tests/legacy/mixin/requester.cpp" "libcaf_core/tests/legacy/mixin/requester.cpp"

# Rebuild the test binary to pick up the updated test file
cmake --build /app/build --target caf-core-legacy-test -j$(nproc)

# Run only the mixin.requester test suite
/app/build/libcaf_core/caf-core-legacy-test -r300 -n -v5 -s "^mixin.requester$"
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
