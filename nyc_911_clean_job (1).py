# -*- coding: utf-8 -*-
"""
NYC 911 — Cleaning job (CSV -> cleaned Parquet)

Reads the raw CSV from HDFS (or local file path), computes:
- response_time_mins
- hour_of_day, day_of_week, is_night, is_weekend
- slow_response (1 if response_time_mins > 10)

Writes cleaned Parquet to the output path.

Example (inside docker):
  /spark/bin/spark-submit /app/spark_jobs/nyc_911_clean_job.py \
    --input  hdfs://namenode:9000/nyc911/raw/nyc_911.csv \
    --output hdfs://namenode:9000/nyc911/clean/
"""

from __future__ import print_function

import argparse

from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col,
    dayofweek,
    hour,
    round as spark_round,
    to_timestamp,
    unix_timestamp,
    when,
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="CSV path (hdfs://... or file:/... or /path)")
    p.add_argument("--output", required=True, help="Parquet output dir (hdfs://... or file:/... or /path)")
    return p.parse_args()


def main():
    args = parse_args()
    spark = SparkSession.builder.appName("NYC911_Clean").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    # Robust-ish CSV read; NYC files can include quoted commas
    df = (
        spark.read.option("header", "true")
        .option("sep", ",")
        .option("quote", '"')
        .option("escape", '"')
        .option("multiLine", "true")
        .csv(args.input)
    )

    # Normalize column names (some exports include quotes/spaces)
    df = df.toDF(*[c.strip().replace('"', "") for c in df.columns])

    # Drop rows missing core categorical predictors / dates
    df = df.dropna(subset=["BORO_NM", "TYP_DESC", "INCIDENT_DATE"])

    # Compute response_time_mins from DISP_TS -> ARRIVD_TS
    df = df.filter(
        col("ARRIVD_TS").isNotNull()
        & col("DISP_TS").isNotNull()
        & (col("ARRIVD_TS") != "")
        & (col("DISP_TS") != "")
    ).withColumn(
        "response_time_mins",
        spark_round(
            (unix_timestamp(col("ARRIVD_TS"), "MM/dd/yyyy hh:mm:ss a") - unix_timestamp(col("DISP_TS"), "MM/dd/yyyy hh:mm:ss a"))
            / 60,
            2,
        ),
    )

    # Remove impossible/bad values
    df = df.filter((col("response_time_mins") > 0) & (col("response_time_mins") < 120))

    # Feature engineering
    df = (
        df.withColumn("hour_of_day", hour(to_timestamp(col("INCIDENT_TIME"), "HH:mm:ss")))
        .withColumn("day_of_week", dayofweek(to_timestamp(col("INCIDENT_DATE"), "MM/dd/yyyy")))
        .withColumn("is_night", when((col("hour_of_day") >= 22) | (col("hour_of_day") <= 6), 1).otherwise(0))
        .withColumn("is_weekend", when(col("day_of_week").isin([1, 7]), 1).otherwise(0))
        .withColumn("slow_response", when(col("response_time_mins") > 10, 1).otherwise(0))
    )

    out = df.select(
        "BORO_NM",
        "TYP_DESC",
        "hour_of_day",
        "day_of_week",
        "is_night",
        "is_weekend",
        "response_time_mins",
        "slow_response",
    )

    out.write.mode("overwrite").parquet(args.output)
    print("Wrote cleaned parquet to: {0}".format(args.output))

    spark.stop()


if __name__ == "__main__":
    main()

