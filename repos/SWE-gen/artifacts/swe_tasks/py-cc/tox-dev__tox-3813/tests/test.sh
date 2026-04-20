#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "tests/session/cmd"
cp "/tests/session/cmd/test_schema.py" "tests/session/cmd/test_schema.py"

source /opt/venv/bin/activate

pytest -xvs tests/session/cmd/test_schema.py
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
