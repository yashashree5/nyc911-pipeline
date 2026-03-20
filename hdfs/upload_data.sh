#!/bin/bash
# NYC 911 Data Upload to HDFS
# Run inside Docker namenode container
# docker exec -it namenode bash

# Step 1 — Create HDFS directory
hdfs dfs -mkdir -p /nyc911/raw

# Step 2 — Upload dataset
# File must already be copied into container:
# docker cp nyc_911.csv namenode:/tmp/nyc_911.csv
hdfs dfs -put /tmp/nyc_911.csv /nyc911/raw/

# Step 3 — Verify upload
hdfs dfs -ls /nyc911/raw/
hdfs dfs -du -h /nyc911/raw/

echo "Upload complete"
