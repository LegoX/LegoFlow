#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "packages/loot-core/src/server/accounts"
cp "/tests/packages/loot-core/src/server/accounts/rules.test.ts" "packages/loot-core/src/server/accounts/rules.test.ts"

cd packages/loot-core
node --max-old-space-size=512 ../../node_modules/.bin/jest -c jest.config.js src/server/accounts/rules.test.ts --coverage=false --runInBand
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
