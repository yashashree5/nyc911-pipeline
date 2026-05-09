from pyspark.sql import SparkSession
from pyspark.sql.functions import col, from_json, when, avg, count
from pyspark.sql.functions import round as spark_round
from pyspark.sql.types import (StructType, StructField, StringType,
                                IntegerType, DoubleType)
from pyspark.ml.feature import StringIndexer, VectorAssembler
from pyspark.ml.classification import GBTClassifier
from pyspark.ml import Pipeline
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.sql.types import DoubleType
from pyspark.sql.functions import udf
from pyspark import StorageLevel

spark = SparkSession.builder.appName("NYC911_KafkaGBT") \
    .master("local[1]") \
    .config("spark.sql.shuffle.partitions", "2") \
    .config("spark.ui.enabled", "false") \
    .getOrCreate()

spark.sparkContext.setLogLevel("WARN")

schema = StructType([
    StructField("BORO_NM",            StringType(),  True),
    StructField("TYP_DESC",           StringType(),  True),
    StructField("hour_of_day",        IntegerType(), True),
    StructField("day_of_week",        IntegerType(), True),
    StructField("is_night",           IntegerType(), True),
    StructField("is_weekend",         IntegerType(), True),
    StructField("response_time_mins", DoubleType(),  True),
    StructField("slow_response",      IntegerType(), True),
])

print("Step 1: Reading from Kafka...")
df = spark.read.format("kafka") \
    .option("kafka.bootstrap.servers", "kafka:29092") \
    .option("subscribe", "nyc911-incidents") \
    .option("startingOffsets", "earliest") \
    .option("endingOffsets", "latest") \
    .load()

parsed = df.select(from_json(col("value").cast("string"), schema).alias("d")).select("d.*") \
    .filter(col("BORO_NM").isNotNull() & col("slow_response").isNotNull()) \
    .withColumn("label", col("slow_response").cast("double")) \
    .limit(2000) \
    .persist(StorageLevel.DISK_ONLY)

n = parsed.count()
print("Step 2: Got " + str(n) + " rows from Kafka")

TOP10 = ["ASSAULT", "ROBBERY", "AMBULANCE CASE EDP", "SHOTS FIRED",
         "AUTO ACCIDENT", "UNCONSCIOUS", "BURGLARY", "DISPUTE",
         "DOMESTIC DISPUTE", "FELONY ASSAULT"]

parsed = parsed.withColumn("TYP_DESC",
    when(col("TYP_DESC").isin(TOP10), col("TYP_DESC")).otherwise("OTHER"))

print("Step 3: Training GBT on Kafka data...")
boro_idx  = StringIndexer(inputCol="BORO_NM",  outputCol="boro_idx",  handleInvalid="keep")
type_idx  = StringIndexer(inputCol="TYP_DESC", outputCol="type_idx",  handleInvalid="keep")
assembler = VectorAssembler(
    inputCols=["boro_idx", "type_idx", "hour_of_day", "day_of_week", "is_night", "is_weekend"],
    outputCol="features", handleInvalid="keep")
gbt = GBTClassifier(labelCol="label", featuresCol="features",
    maxIter=3, maxDepth=2, maxBins=32, seed=42)
pipeline = Pipeline(stages=[boro_idx, type_idx, assembler, gbt])

train, test = parsed.randomSplit([0.8, 0.2], seed=42)
train = train.persist(StorageLevel.DISK_ONLY)
test  = test.persist(StorageLevel.DISK_ONLY)
train.count()
test.count()

model = pipeline.fit(train)
model.save("/tmp/nyc911_gbt_model")
print("Model saved to /tmp/nyc911_gbt_model")
print("Step 4: Model trained. Scoring...")

try:
    get_prob = udf(lambda v: float(v[1]), DoubleType())
    scored = model.transform(test) \
        .withColumn("slow_prob", get_prob(col("probability"))) \
        .persist(StorageLevel.DISK_ONLY)

    scored_n = scored.count()
    print("Step 5: Scored " + str(scored_n) + " rows")

    auc = BinaryClassificationEvaluator(
        labelCol="label", metricName="areaUnderROC").evaluate(scored)
    print("Step 6: AUC computed: " + str(auc))

    print("")
    print("=" * 55)
    print("KAFKA -> SPARK STREAMING -> GBT RESULTS")
    print("=" * 55)
    print("  Rows from Kafka stream : " + str(n))
    print("  AUC-ROC                : " + str(round(auc, 4)))
    print("=" * 55)
    print("")
    print("BOROUGH RISK TABLE:")
    scored.groupBy("BORO_NM").agg(
        count("*").alias("calls"),
        spark_round(avg("slow_prob") * 100, 1).alias("avg_risk_pct"),
        spark_round(avg("label") * 100, 1).alias("actual_slow_pct")
    ).orderBy(col("avg_risk_pct").desc()).show(truncate=False)
    print("SUCCESS.")

except Exception as e:
    print("ERROR during scoring: " + str(e))
    import traceback
    traceback.print_exc()

finally:
    spark.stop()