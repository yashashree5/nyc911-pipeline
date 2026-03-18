#!/bin/bash
# Hive 3.1.2 Setup inside bde2020 Docker Container
# Fixes Guava conflict between Hive 3.1.2 and Hadoop 3.2.1

# Step 1 — Download Hive on your Mac first
# curl -L https://archive.apache.org/dist/hive/hive-3.1.2/apache-hive-3.1.2-bin.tar.gz -o apache-hive-3.1.2-bin.tar.gz

# Step 2 — Copy into container from Mac
# docker cp apache-hive-3.1.2-bin.tar.gz namenode:/tmp/

# Step 3 — Inside container: extract
cd /tmp
tar -xzf apache-hive-3.1.2-bin.tar.gz
mv apache-hive-3.1.2-bin /opt/hive

# Step 4 — Set environment variables
export HIVE_HOME=/opt/hive
export PATH=$PATH:$HIVE_HOME/bin

# Step 5 — Fix Guava JAR conflict
# Hive 3.1.2 ships with guava-19 but Hadoop 3.2.1 needs guava-27
rm /opt/hive/lib/guava-19.0.jar
cp /opt/hadoop-3.2.1/share/hadoop/common/lib/guava-27.0-jre.jar /opt/hive/lib/

# Step 6 — Initialize Derby metastore
schematool -initSchema -dbType derby

# Step 7 — Open Hive shell
hive

echo "Hive is ready"
