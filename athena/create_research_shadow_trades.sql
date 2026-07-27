-- ═══════════════════════════════════════════════════════════════════════════════
-- CREATE EXTERNAL TABLE: research_shadow_trades
-- Database: trading_bot
-- S3 Location: s3://trading-bot-data-mk1/research_shadow_trades/
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.research_shadow_trades (
    schema_version STRING,
    source STRING,
    identity STRUCT<
        trade_id: STRING,
        correlation_id: STRING,
        symbol: STRING,
        strategy_id: STRING,
        cycle_id: STRING
    >,
    decision_snapshot STRUCT<
        timestamp_decision_utc: DOUBLE,
        entry_intent_price: DOUBLE,
        stop_loss_intent: DOUBLE,
        take_profit_intent: DOUBLE,
        direction: STRING,
        position_size: DOUBLE,
        risk_config_snapshot: STRUCT<
            risk_price_distance: DOUBLE,
            risk_pips: DOUBLE,
            reward_risk_ratio: DOUBLE
        >,
        pattern: STRING,
        score: DOUBLE,
        execution_context_ref: STRING
    >,
    simulation_environment STRUCT<
        htf_snapshot: STRING,
        entry_bar_index: INT,
        events_ref: STRUCT<
            bar_time: DOUBLE
        >
    >,
    simulated_outcome STRUCT<
        exit_price: DOUBLE,
        exit_timestamp: DOUBLE,
        pnl_r_multiple: DOUBLE,
        mfe_r: DOUBLE,
        mae_r: DOUBLE,
        exit_reason: STRING,
        bars_held: INT,
        trade_state_progression: ARRAY<
            STRUCT<
                bar: INT,
                r: DOUBLE,
                close: DOUBLE
            >
        >
    >
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'ignore.malformed.json' = 'true',
    'case.insensitive' = 'true'
)
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://trading-bot-data-mk1/research_shadow_trades/'
TBLPROPERTIES ('has_encrypted_data'='false');


-- ═══════════════════════════════════════════════════════════════════════════════
-- VALIDATION QUERIES
-- Run these after table creation to confirm data is accessible.
-- ═══════════════════════════════════════════════════════════════════════════════

-- 1. Record count
SELECT COUNT(*) AS total_research_shadow_trades
FROM trading_bot.research_shadow_trades;

-- 2. Sample record
SELECT *
FROM trading_bot.research_shadow_trades
LIMIT 1;

-- 3. Verify key fields are queryable
SELECT
    identity.symbol,
    identity.trade_id,
    identity.correlation_id,
    decision_snapshot.pattern,
    decision_snapshot.direction,
    decision_snapshot.entry_intent_price,
    decision_snapshot.stop_loss_intent,
    decision_snapshot.take_profit_intent,
    decision_snapshot.score,
    simulated_outcome.pnl_r_multiple,
    simulated_outcome.exit_reason,
    simulated_outcome.bars_held,
    simulated_outcome.mfe_r,
    simulated_outcome.mae_r
FROM trading_bot.research_shadow_trades
LIMIT 5;

-- 4. Performance summary
SELECT
    COUNT(*) AS total_trades,
    SUM(CASE WHEN simulated_outcome.pnl_r_multiple > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN simulated_outcome.pnl_r_multiple < 0 THEN 1 ELSE 0 END) AS losses,
    ROUND(AVG(simulated_outcome.pnl_r_multiple), 4) AS avg_r,
    ROUND(SUM(simulated_outcome.pnl_r_multiple), 2) AS total_r,
    ROUND(
        CAST(SUM(CASE WHEN simulated_outcome.pnl_r_multiple > 0 THEN 1 ELSE 0 END) AS DOUBLE)
        / NULLIF(COUNT(*), 0), 4
    ) AS win_rate
FROM trading_bot.research_shadow_trades;

-- 5. Per-pattern breakdown
SELECT
    decision_snapshot.pattern,
    COUNT(*) AS trades,
    ROUND(AVG(simulated_outcome.pnl_r_multiple), 4) AS avg_r,
    ROUND(SUM(simulated_outcome.pnl_r_multiple), 2) AS total_r,
    ROUND(
        CAST(SUM(CASE WHEN simulated_outcome.pnl_r_multiple > 0 THEN 1 ELSE 0 END) AS DOUBLE)
        / NULLIF(COUNT(*), 0), 4
    ) AS win_rate
FROM trading_bot.research_shadow_trades
GROUP BY decision_snapshot.pattern
ORDER BY total_r DESC;

-- 6. Per-symbol breakdown
SELECT
    identity.symbol,
    COUNT(*) AS trades,
    ROUND(AVG(simulated_outcome.pnl_r_multiple), 4) AS avg_r,
    ROUND(SUM(simulated_outcome.pnl_r_multiple), 2) AS total_r
FROM trading_bot.research_shadow_trades
GROUP BY identity.symbol
ORDER BY total_r DESC;

-- 7. Per-candidate breakdown (which research candidates are performing)
SELECT
    identity.correlation_id AS candidate_id,
    COUNT(*) AS trades,
    ROUND(AVG(simulated_outcome.pnl_r_multiple), 4) AS avg_r,
    ROUND(SUM(simulated_outcome.pnl_r_multiple), 2) AS total_r,
    ROUND(
        CAST(SUM(CASE WHEN simulated_outcome.pnl_r_multiple > 0 THEN 1 ELSE 0 END) AS DOUBLE)
        / NULLIF(COUNT(*), 0), 4
    ) AS win_rate
FROM trading_bot.research_shadow_trades
GROUP BY identity.correlation_id
ORDER BY total_r DESC;
