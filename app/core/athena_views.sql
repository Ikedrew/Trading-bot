-- ═══════════════════════════════════════════════════════════════════════════════
-- ATHENA VIEWS — Schema-versioned event access layer
-- ═══════════════════════════════════════════════════════════════════════════════
-- These views provide safe, stable query interfaces across schema evolution.
-- All canonical fields are top-level columns — no json_extract_scalar needed.
--
-- Table: bot_events (backed by S3 Hive-partitioned JSONL)
--   s3://trading-bot-data-mk1/events/symbol={SYMBOL}/date={YYYY-MM-DD}/
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── CURRENT SCHEMA VIEW (v2 only — canonical events) ────────────────────────
-- Use this for all standard analytics queries.
-- Guaranteed: all canonical fields are non-empty strings.

CREATE OR REPLACE VIEW bot_events_v2 AS
SELECT
    ts_utc_ms,
    type,
    symbol,
    source,
    schema_version,
    feature_version,
    -- Canonical immutable fields (resolved at emit-time, never re-derived)
    pattern,
    regime,
    bias,
    side,
    guard_result
FROM bot_events
WHERE schema_version = 2;


-- ─── FULL HISTORY VIEW (v1 + v2 unified) ────────────────────────────────────
-- Use this for historical analysis spanning pre/post canonical normalisation.
-- schema_class column indicates whether the event was natively v2 or migrated.

CREATE OR REPLACE VIEW bot_events_all AS
SELECT
    ts_utc_ms,
    type,
    symbol,
    source,
    schema_version,
    feature_version,
    pattern,
    regime,
    bias,
    side,
    guard_result,
    CASE
        WHEN schema_version = 1 THEN 'LEGACY'
        WHEN schema_version = 2 THEN 'CANONICAL'
        ELSE 'UNKNOWN_VERSION'
    END AS schema_class
FROM bot_events;


-- ─── FEATURE VERSION COMPARISON VIEW ─────────────────────────────────────────
-- Use this for A/B testing and strategy evolution analysis.
-- Groups events by feature_version for side-by-side comparison.

CREATE OR REPLACE VIEW bot_events_by_feature AS
SELECT
    feature_version,
    type,
    symbol,
    pattern,
    regime,
    bias,
    side,
    guard_result,
    ts_utc_ms
FROM bot_events
WHERE schema_version = 2;


-- ─── PATTERN DISTRIBUTION (simple, fast, no JSON extraction) ─────────────────

-- Example: Pattern frequency
-- SELECT pattern, COUNT(*) AS cnt
-- FROM bot_events_v2
-- GROUP BY pattern
-- ORDER BY cnt DESC;

-- Example: Guard results by regime
-- SELECT regime, guard_result, COUNT(*) AS cnt
-- FROM bot_events_v2
-- WHERE type = 'RISK_CHECK'
-- GROUP BY regime, guard_result;

-- Example: Side distribution per symbol
-- SELECT symbol, side, COUNT(*) AS cnt
-- FROM bot_events_v2
-- WHERE type IN ('EXECUTION', 'DECISION')
-- GROUP BY symbol, side;

-- ─── FEATURE VERSION COMPARISON QUERIES ──────────────────────────────────────

-- Example: Compare performance across feature versions
-- SELECT
--     feature_version,
--     pattern,
--     COUNT(*) AS trades
-- FROM bot_events_by_feature
-- WHERE type = 'EXECUTION'
-- GROUP BY feature_version, pattern
-- ORDER BY feature_version, trades DESC;

-- Example: Compare strategy evolution (guard approval rates)
-- SELECT
--     feature_version,
--     guard_result,
--     COUNT(*) AS cnt,
--     ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY feature_version), 2) AS pct
-- FROM bot_events_by_feature
-- WHERE type = 'RISK_CHECK'
-- GROUP BY feature_version, guard_result;

-- Example: Regime distribution by feature version
-- SELECT
--     feature_version,
--     regime,
--     COUNT(*) AS cnt
-- FROM bot_events_by_feature
-- GROUP BY feature_version, regime
-- ORDER BY feature_version, cnt DESC;


-- ═══════════════════════════════════════════════════════════════════════════════
-- CURATED EVENT LAYER — Flat analytics table for strategy research
-- ═══════════════════════════════════════════════════════════════════════════════
-- Source: s3://trading-bot-data-mk1/events/curated/
-- Format: Flat JSONL (one event per line, no nesting)
-- Schema: Strict 10-column structure, Glue-catalogued
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── TABLE DDL (run in Athena or auto-created by Glue crawler) ───────────────

CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.curated_events (
    `timestamp` string,
    `symbol` string,
    `event_type` string,
    `pattern` string,
    `htf_bias` string,
    `liquidity_swept` boolean,
    `bos_confirmed` boolean,
    `atr_regime` string,
    `pnl` double,
    `trade_id` string
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'serialization.format' = '1'
)
LOCATION 's3://trading-bot-data-mk1/events/curated/'
TBLPROPERTIES ('has_encrypted_data'='false');


-- ═══════════════════════════════════════════════════════════════════════════════
-- CURATED ANALYTICS QUERIES
-- ═══════════════════════════════════════════════════════════════════════════════

-- ─── Pattern Performance (expectancy per pattern) ────────────────────────────

-- SELECT pattern, AVG(pnl) AS expectancy, COUNT(*) AS trades
-- FROM trading_bot.curated_events
-- WHERE pnl != 0
-- GROUP BY pattern
-- ORDER BY expectancy DESC;


-- ─── Market Context Analysis (bias impact on PnL) ───────────────────────────

-- SELECT htf_bias, AVG(pnl) AS avg_pnl, COUNT(*) AS cnt
-- FROM trading_bot.curated_events
-- WHERE pnl != 0
-- GROUP BY htf_bias;


-- ─── Execution Quality (liquidity + BOS impact) ─────────────────────────────

-- SELECT liquidity_swept, bos_confirmed, AVG(pnl) AS avg_pnl, COUNT(*) AS cnt
-- FROM trading_bot.curated_events
-- WHERE pnl != 0
-- GROUP BY liquidity_swept, bos_confirmed;


-- ─── Regime Analysis (ATR regime vs performance) ────────────────────────────

-- SELECT atr_regime, COUNT(*) AS cnt, AVG(pnl) AS avg_pnl
-- FROM trading_bot.curated_events
-- GROUP BY atr_regime;


-- ─── Symbol Breakdown ────────────────────────────────────────────────────────

-- SELECT symbol, pattern, COUNT(*) AS trades, AVG(pnl) AS avg_pnl
-- FROM trading_bot.curated_events
-- WHERE pnl != 0
-- GROUP BY symbol, pattern
-- ORDER BY trades DESC;


-- ─── Win Rate by Pattern ─────────────────────────────────────────────────────

-- SELECT
--     pattern,
--     COUNT(*) AS total_trades,
--     SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
--     SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) AS losses,
--     ROUND(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS win_rate_pct,
--     AVG(pnl) AS expectancy
-- FROM trading_bot.curated_events
-- WHERE pnl != 0
-- GROUP BY pattern
-- HAVING COUNT(*) >= 5
-- ORDER BY win_rate_pct DESC;
