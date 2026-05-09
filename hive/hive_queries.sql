-- ================================================
-- NYC 911 Emergency Response — Hive Analytics
-- Independent Tool: Apache Hive (not in DATA 228)
-- Author: Yashashree Shinde
-- Date: March 17 2026
-- ================================================

CREATE DATABASE IF NOT EXISTS nyc911db;
USE nyc911db;

CREATE EXTERNAL TABLE IF NOT EXISTS incidents (
    CAD_EVNT_ID     STRING,
    CREATE_DATE     STRING,
    INCIDENT_DATE   STRING,
    INCIDENT_TIME   STRING,
    NYPD_PCT_CD     STRING,
    BORO_NM         STRING,
    PATRL_BORO_NM   STRING,
    GEO_CD_X        STRING,
    GEO_CD_Y        STRING,
    RADIO_CODE      STRING,
    TYP_DESC        STRING,
    CIP_JOBS        STRING,
    ADD_TS          STRING,
    DISP_TS         STRING,
    ARRIVD_TS       STRING,
    CLOSNG_TS       STRING,
    Latitude        STRING,
    Longitude       STRING
)
ROW FORMAT DELIMITED
FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 'hdfs://namenode:9000/nyc911/raw/'
TBLPROPERTIES ("skip.header.line.count"="1");

-- Query 1: Borough breakdown
-- Result: Brooklyn 2143896, Manhattan 1855338,
--         Bronx 1402043, Queens 1343488,
--         Staten Island 293937
SELECT
    BORO_NM,
    COUNT(*) as total_incidents
FROM incidents
WHERE BORO_NM IS NOT NULL
AND BORO_NM != ''
GROUP BY BORO_NM
ORDER BY total_incidents DESC;

-- Query 2: Top 10 incident types
-- Result: Visibility Patrol 709886 most common
SELECT
    TYP_DESC,
    COUNT(*) as frequency
FROM incidents
WHERE TYP_DESC IS NOT NULL
AND TYP_DESC != ''
GROUP BY TYP_DESC
ORDER BY frequency DESC
LIMIT 10;

-- Query 3: Incidents by hour of day
-- Result: Hour 18 busiest (402246), Hour 04 quietest (161874)
SELECT
    SUBSTR(INCIDENT_TIME, 1, 2) as hour_of_day,
    COUNT(*) as incidents
FROM incidents
WHERE INCIDENT_TIME IS NOT NULL
GROUP BY SUBSTR(INCIDENT_TIME, 1, 2)
ORDER BY hour_of_day;

-- Query 4: Data quality check
-- Result: 7038863 total, 0 null borough,
--         0 null type, 1551083 null arrival
SELECT
    COUNT(*) as total_rows,
    SUM(CASE WHEN BORO_NM = ''
        OR BORO_NM IS NULL
        THEN 1 ELSE 0 END) as null_borough,
    SUM(CASE WHEN TYP_DESC = ''
        OR TYP_DESC IS NULL
        THEN 1 ELSE 0 END) as null_type,
    SUM(CASE WHEN ARRIVD_TS = ''
        OR ARRIVD_TS IS NULL
        THEN 1 ELSE 0 END) as null_arrival,
    SUM(CASE WHEN DISP_TS = ''
        OR DISP_TS IS NULL
        THEN 1 ELSE 0 END) as null_dispatch
FROM incidents;

-- Query 5: Borough + missing arrival analysis
SELECT
    BORO_NM,
    COUNT(*) as total_incidents,
    SUM(CASE WHEN ARRIVD_TS != ''
        AND ARRIVD_TS IS NOT NULL
        THEN 1 ELSE 0 END) as has_arrival,
    SUM(CASE WHEN ARRIVD_TS = ''
        OR ARRIVD_TS IS NULL
        THEN 1 ELSE 0 END) as missing_arrival
FROM incidents
WHERE BORO_NM IS NOT NULL
AND BORO_NM != ''
GROUP BY BORO_NM
ORDER BY total_incidents DESC;
