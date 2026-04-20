#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "unittests/mat"
cp "/tests/unittests/mat/4C_inelastic_defgrad_factors_test.cpp" "unittests/mat/4C_inelastic_defgrad_factors_test.cpp"
mkdir -p "unittests/mat/vplast"
cp "/tests/unittests/mat/vplast/4C_vplast_reform_johnsoncook_test.cpp" "unittests/mat/vplast/4C_vplast_reform_johnsoncook_test.cpp"

# Rebuild test targets after copying updated test files
cmake --build /app/build --target unittests_mat unittests_mat_vplast -- -j $(nproc)
build_status=$?

if [ $build_status -ne 0 ]; then
  echo 0 > /logs/verifier/reward.txt
  exit 1
fi

# Run the specific test targets
cd /app/build
ctest -R "^unittests_mat$|^unittests_mat_vplast$" --output-on-failure
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
