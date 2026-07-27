-- ═══════════════════════════════════════════════════════════════════════════════
-- STRATEGY OBSERVATIONS — Athena Table Definition
-- ═══════════════════════════════════════════════════════════════════════════════
-- Source: s3://trading-bot-data-mk1/strategy_observations/
-- Format: Hive-partitioned JSONL (one observation per line)
-- Partition: symbol={SYMBOL}/date={YYYY-MM-DD}/
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── TABLE DDL ───────────────────────────────────────────────────────────────

CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.strategy_observations (
    `schema_version`        string,
    `observation_id`        string,
    `timestamp_utc`         double,
    `cycle_id`              int,
    -- Market environment
    `market_phase`          string,
    `h4_regime`             string,
    `h1_bias`               string,
    `direction`             string,
    -- Pattern
    `detected_pattern`      string,
    `pattern_in_triggers`   boolean,
    -- Strategy classification
    `strategy_family`       string,
    `candidate_strategies`  array<struct<strategy_id:string, eligible:boolean, status:string, confidence:double>>,
    -- Condition evaluation
    `conditions_passed`     int,
    `conditions_failed`     int,
    `conditions_missing`    int,
    `missing_data`          array<string>,
    `evaluation_status`     string,
    `confidence`            double,
    -- Context quality
    `tradability_score`     double,
    `eligible_by_phase`     boolean
)
PARTITIONED BY (
    `symbol` string,
    `date`   string
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'serialization.format' = '1'
)
LOCATION 's3://trading-bot-data-mk1/strategy_observations/'
TBLPROPERTIES ('has_encrypted_data'='false');


-- ─── REPAIR PARTITIONS (run after new data arrives) ──────────────────────────

-- MSCK REPAIR TABLE trading_bot.strategy_observations;


-- ═══════════════════════════════════════════════════════════════════════════════
-- RESEARCH QUERIES
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── Q1: Strategy occurrence frequency ───────────────────────────────────────
-- "How often do strategy conditions occur?"

-- SELECT
--     strategy_family,
--     evaluation_status,
--     COUNT(*) AS observations,
--     AVG(confidence) AS avg_confidence
-- FROM trading_bot.strategy_observations
-- GROUP BY strategy_family, evaluation_status
-- ORDER BY observations DESC;


-- ─── Q2: Phase × family distribution ─────────────────────────────────────────
-- "Which strategies appear in which phases?"

-- SELECT
--     market_phase,
--     strategy_family,
--     COUNT(*) AS observations,
--     SUM(CASE WHEN evaluation_status = 'FULLY_MET' THEN 1 ELSE 0 END) AS fully_met
-- FROM trading_bot.strategy_observations
-- GROUP BY market_phase, strategy_family
-- ORDER BY market_phase, observations DESC;


-- ─── Q3: Condition completeness ──────────────────────────────────────────────
-- "How often are conditions fully evaluable vs missing data?"

-- SELECT
--     evaluation_status,
--     COUNT(*) AS cnt,
--     AVG(conditions_passed) AS avg_passed,
--     AVG(conditions_failed) AS avg_failed,
--     AVG(conditions_missing) AS avg_missing
-- FROM trading_bot.strategy_observations
-- GROUP BY evaluation_status;


-- ─── Q4: Pattern × strategy alignment ────────────────────────────────────────
-- "Which patterns trigger which strategy families?"

-- SELECT
--     detected_pattern,
--     strategy_family,
--     COUNT(*) AS cnt,
--     SUM(CASE WHEN pattern_in_triggers THEN 1 ELSE 0 END) AS in_triggers
-- FROM trading_bot.strategy_observations
-- WHERE detected_pattern != ''
-- GROUP BY detected_pattern, strategy_family
-- ORDER BY cnt DESC;


-- ─── Q5: Today's observations ────────────────────────────────────────────────
-- "What strategies were observed today?"

-- SELECT
--     symbol,
--     strategy_family,
--     market_phase,
--     h4_regime,
--     evaluation_status,
--     confidence,
--     detected_pattern,
--     timestamp_utc
-- FROM trading_bot.strategy_observations
-- WHERE date = '2026-07-27'
-- ORDER BY timestamp_utc DESC
-- LIMIT 50;


-- ─── Q6: High-confidence observations ───────────────────────────────────────
-- "When were strategy conditions fully met with high confidence?"

-- SELECT
--     observation_id,
--     symbol,
--     strategy_family,
--     market_phase,
--     h4_regime,
--     detected_pattern,
--     confidence,
--     conditions_passed,
--     timestamp_utc
-- FROM trading_bot.strategy_observations
-- WHERE evaluation_status = 'FULLY_MET'
--   AND confidence >= 0.8
-- ORDER BY timestamp_utc DESC;


-- ─── Q7: Join with trade_truth for outcome analysis ──────────────────────────
-- "When strategy conditions were met, what was the trade outcome?"
-- NOTE: This requires outcome linkage via entity_id or temporal proximity.
--       Full implementation requires the Outcome Linker persistence layer.

-- SELECT
--     so.strategy_family,
--     so.market_phase,
--     so.evaluation_status,
--     so.confidence,
--     tt.r_multiple,
--     tt.exit_reason
-- FROM trading_bot.strategy_observations so
-- JOIN trading_bot.shadow_trades_v2 tt
--     ON so.symbol = tt.symbol
--     AND so.timestamp_utc BETWEEN tt.entry_time - 300 AND tt.entry_time + 300
-- WHERE so.evaluation_status = 'FULLY_MET';
