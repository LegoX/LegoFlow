#!/bin/bash

cd /app/src

# Set environment variables for tests
export CI=true
export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64

# Copy HEAD test files from /tests (overwrites BASE state)
mkdir -p "naming/src/test/java/com/alibaba/nacos/naming/monitor"
cp "/tests/naming/src/test/java/com/alibaba/nacos/naming/monitor/MetricsMonitorTest.java" "naming/src/test/java/com/alibaba/nacos/naming/monitor/MetricsMonitorTest.java"

# Run ONLY the specific test file from the PR
mvn test -pl naming --also-make -Dtest=MetricsMonitorTest -B -DfailIfNoTests=false -Dorg.slf4j.simpleLogger.log.org.apache.maven.cli.transfer.Slf4jMavenTransferListener=warn
test_status=$?

if [ $test_status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
exit "$test_status"
