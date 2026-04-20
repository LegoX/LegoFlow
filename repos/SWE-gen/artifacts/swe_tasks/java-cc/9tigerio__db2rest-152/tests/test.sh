#!/bin/bash

cd /app/src

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "src/test/java/com/homihq/db2rest/rest"
cp "/tests/java/com/homihq/db2rest/rest/MySQLBulkCreateControllerTest.java" "src/test/java/com/homihq/db2rest/rest/MySQLBulkCreateControllerTest.java"
mkdir -p "src/test/java/com/homihq/db2rest/rest"
cp "/tests/java/com/homihq/db2rest/rest/MySQLCreateControllerTest.java" "src/test/java/com/homihq/db2rest/rest/MySQLCreateControllerTest.java"
mkdir -p "src/test/java/com/homihq/db2rest/rest"
cp "/tests/java/com/homihq/db2rest/rest/PgBulkCreateControllerTest.java" "src/test/java/com/homihq/db2rest/rest/PgBulkCreateControllerTest.java"
mkdir -p "src/test/java/com/homihq/db2rest/rest"
cp "/tests/java/com/homihq/db2rest/rest/PgCreateControllerTest.java" "src/test/java/com/homihq/db2rest/rest/PgCreateControllerTest.java"
mkdir -p "src/test/resources/mysql"
cp "/tests/resources/mysql/mysql-sakila.sql" "src/test/resources/mysql/mysql-sakila.sql"
mkdir -p "src/test/resources/pg"
cp "/tests/resources/pg/postgres-sakila.sql" "src/test/resources/pg/postgres-sakila.sql"

./mvnw test -Dtest=MySQLBulkCreateControllerTest,MySQLCreateControllerTest,PgBulkCreateControllerTest,PgCreateControllerTest -DfailIfNoTests=false
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
