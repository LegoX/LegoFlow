#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "test/integration/targets/apt_repository/tasks"
cp "/tests/integration/targets/apt_repository/tasks/apt.yml" "test/integration/targets/apt_repository/tasks/apt.yml"
mkdir -p "test/integration/targets/roles_arg_spec"
cp "/tests/integration/targets/roles_arg_spec/test.yml" "test/integration/targets/roles_arg_spec/test.yml"
mkdir -p "test/units/_internal/templating"
cp "/tests/units/_internal/templating/test_templar.py" "test/units/_internal/templating/test_templar.py"
mkdir -p "test/units/module_utils/common/validation"
cp "/tests/units/module_utils/common/validation/test_check_type_str.py" "test/units/module_utils/common/validation/test_check_type_str.py"

# Run only the .py unit test files (skip .yml integration test files which need special ansible tooling)
source /opt/venv/bin/activate
python -m pytest -xvs \
    test/units/_internal/templating/test_templar.py \
    test/units/module_utils/common/validation/test_check_type_str.py
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
