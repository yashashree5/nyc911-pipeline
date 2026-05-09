# -*- coding: utf-8 -*-
"""
NYC 911 — Spark Structured Streaming + GBT Scoring
====================================================
Reads incidents from Kafka in real-time, applies the saved GBT model,
and writes scored records to a Parquet sink (which the dashboard reads).

Pipeline:
    Kafka topic "nyc911-incidents"
        → parse JSON
        → StringIndexer (TYP_DESC, BORO_NM)
        → VectorAssembler
        → GBT PipelineModel.transform()
        → write to /tmp/nyc911_scored/

Usage:
    spark-submit \
        --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 \
        streaming_consumer.py \
        --model /path/to/saved_gbt_model \
        --bootstrap localhost:9092

Why Structured Streaming?
    - Micro-batch semantics give exactly-once guarantees with Kafka offsets
    - Stateful aggregations (borough risk windows) auto-recover from failure
    - The same Pipeline object trained in batch works unchanged on a stream
"""

import argparse
import logging
from pyspark.sql import SparkSession
from pyspark.sql.functions import (
    col, from_json, window, count, avg,
    sum as spark_sum, current_timestamp, udf,
)
from pyspark.sql.types import (
    StructType, StructField,
    StringType, DoubleType, IntegerType
)
from pyspark.ml import PipelineModel

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# MLlib probability is a Vector (dense or sparse); SQL bracket indexing fails on vectors.
_get_p_slow = udf(lambda v: float(v[1]) if v is not None else 0.0, DoubleType())

# ──────────────────────────────────────────────
# Kafka message schema (mirrors the producer dict)
# ──────────────────────────────────────────────
INCIDENT_SCHEMA = StructType([
    StructField("BORO_NM",            StringType(),  True),
    StructField("TYP_DESC",           StringType(),  True),
    StructField("hour_of_day",        IntegerType(), True),
    StructField("day_of_week",        IntegerType(), True),
    StructField("is_night",           IntegerType(), True),
    StructField("is_weekend",         IntegerType(), True),
    StructField("response_time_mins", DoubleType(),  True),
    StructField("slow_response",      IntegerType(), True),
    StructField("INCIDENT_DATE",      StringType(),  True),
    StructField("INCIDENT_TIME",      StringType(),  True),
    StructField("event_ts",           StringType(),  True),
])


def build_spark(app_name: str) -> SparkSession:
    return (
        SparkSession.builder
        .appName(app_name)
        # Structured Streaming keeps offsets in checkpoint; tolerate broker lag
        .config("spark.sql.shuffle.partitions", "10")
        .config("spark.streaming.stopGracefullyOnShutdown", "true")
        .getOrCreate()
    )


def read_kafka(spark: SparkSession, bootstrap: str, topic: str):
    """Return a streaming DataFrame from Kafka."""
    return (
        spark.readStream
        .format("kafka")
        .option("kafka.bootstrap.servers", bootstrap)
        .option("subscribe", topic)
        # Start from the latest offset when the job first starts.
        # After a restart, Structured Streaming replays from the checkpoint.
        .option("startingOffsets", "latest")
        # Don't let a slow batch starve the next trigger
        .option("maxOffsetsPerTrigger", 5000)
        .load()
    )


def parse_messages(raw_df):
    """Cast Kafka value bytes → JSON string → struct columns."""
    return (
        raw_df
        .select(
            from_json(
                col("value").cast("string"),
                INCIDENT_SCHEMA
            ).alias("data")
        )
        .select("data.*")
    )


def score_stream(parsed_df, model: PipelineModel):
    """
    Apply the trained GBT PipelineModel.
    The model was trained with:
        stages = [borough_indexer, type_indexer, assembler, gbt]
    so it expects BORO_NM and TYP_DESC as raw strings — no pre-processing needed here.
    Output columns added by the model:
        prediction          (0.0 = fast response, 1.0 = slow)
        probability         (Vector: index 1 = p_slow)
    """
    return model.transform(parsed_df)


def write_scored_parquet(scored_df, checkpoint_dir: str, output_dir: str):
    """
    Append scored records to a Parquet sink partitioned by borough.
    The Streamlit dashboard tails this directory.
    """
    return (
        scored_df
        .select(
            "BORO_NM", "TYP_DESC",
            "hour_of_day", "day_of_week",
            "is_night", "is_weekend",
            "response_time_mins", "slow_response",
            "prediction",
            _get_p_slow(col("probability")).alias("p_slow"),
            "event_ts"
        )
        .writeStream
        .format("parquet")
        .option("path", output_dir)
        .option("checkpointLocation", checkpoint_dir + "/parquet")
        .partitionBy("BORO_NM")
        .trigger(processingTime="20 seconds")   # micro-batch every 20 s
        .outputMode("append")
        .start()
    )


def write_borough_risk_console(scored_df, checkpoint_dir: str):
    """
    Rolling 5-minute borough risk window → console (for debugging).
    Replace .format("console") with a memory sink + Streamlit query for production.
    """
    windowed = (
        scored_df
        .withColumn("ts", current_timestamp())
        .groupBy(
            window(col("ts"), "5 minutes", "1 minute"),
            "BORO_NM"
        )
        .agg(
            count("*").alias("incident_count"),
            avg("response_time_mins").alias("avg_response_mins"),
            (spark_sum("prediction") / count("*") * 100).alias("pct_slow_predicted"),
        )
    )

    return (
        windowed.writeStream
        .format("console")
        .option("truncate", False)
        .option("checkpointLocation", checkpoint_dir + "/console")
        .outputMode("update")
        .trigger(processingTime="20 seconds")
        .start()
    )


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NYC 911 Streaming Consumer")
    parser.add_argument("--model",      required=True,
                        help="Path to saved GBT PipelineModel directory")
    parser.add_argument("--bootstrap",  default="localhost:9092",
                        help="Kafka bootstrap servers")
    parser.add_argument("--topic",      default="nyc911-incidents")
    parser.add_argument("--checkpoint", default="/tmp/nyc911_checkpoints")
    parser.add_argument("--output",     default="/tmp/nyc911_scored")
    args = parser.parse_args()

    spark = build_spark("NYC911_StreamingScorer")
    spark.sparkContext.setLogLevel("WARN")

    log.info("Loading GBT PipelineModel from: %s", args.model)
    model = PipelineModel.load(args.model)

    raw_df     = read_kafka(spark, args.bootstrap, args.topic)
    parsed_df  = parse_messages(raw_df)
    scored_df  = score_stream(parsed_df, model)

    # Two concurrent write streams
    q1 = write_scored_parquet(scored_df, args.checkpoint, args.output)
    q2 = write_borough_risk_console(scored_df, args.checkpoint)

    log.info("Streaming queries started. Press Ctrl+C to stop.")
    spark.streams.awaitAnyTermination()
