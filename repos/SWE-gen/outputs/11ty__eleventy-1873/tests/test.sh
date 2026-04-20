#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "test"
cp "/tests/TemplateTest.js" "test/TemplateTest.js"
mkdir -p "test"
cp "/tests/TemplateTest_Permalink.js" "test/TemplateTest_Permalink.js"
mkdir -p "test/slugify-filter"
cp "/tests/slugify-filter/comma.njk" "test/slugify-filter/comma.njk"
mkdir -p "test/slugify-filter"
cp "/tests/slugify-filter/slug-options.njk" "test/slugify-filter/slug-options.njk"
mkdir -p "test/slugify-filter"
cp "/tests/slugify-filter/slugify-options.njk" "test/slugify-filter/slugify-options.njk"
mkdir -p "test/slugify-filter"
cp "/tests/slugify-filter/test.njk" "test/slugify-filter/test.njk"

# Run only the specific test files using AVA directly (avoids running entire suite)
npx ava test/TemplateTest.js test/TemplateTest_Permalink.js --verbose
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
