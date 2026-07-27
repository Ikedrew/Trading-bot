-- ═══════════════════════════════════════════════════════════════════════════════
-- REGIME AUTHORITY ANALYSIS — Migration 1.5 Validation Queries
-- Database: trading_bot
-- Table: decision_trace (existing)
-- ═══════════════════════════════════════════════════════════════════════════════
-- 
-- These queries support pre-migration traces (no regime_source field → NULL)
-- and post-migration traces (regime_source = 'H4_MARKET_CONTEXT' or 'M5_CLASSIFIER').
--
-- Historical traces: regime_source will be NULL (labeled PRE_MIGRATION in reports)
-- New traces: regime_source, regime_timeframe populated automatically
-- ═══════════════════════════════════════════════════════════════════════════════


-- ─── 1. REGIME SOURCE DISTRIBUTION ───────────────────────────────────────────
-- Shows what percentage of decisions use H4 vs M5 vs pre-migration

SELECT
    COALESCE(regime_source, 'PRE_MIGRATION') AS regime_source,
    COALESCE(regime_timeframe, '') AS regime_timeframe,
    COUNT(*) AS decision_count,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS percentage
FROM trading_bot.decision_trace
GROUP BY 1, 2
ORDER BY decision_count DESC;


-- ─── 2. REGIME DISTRIBUTION BY SOURCE ────────────────────────────────────────
-- Shows regime breakdown within each source (H4 should show more variation)

SELECT
    COALESCE(regime_source, 'PRE_MIGRATION') AS source,
    regime,
    COUNT(*) AS cnt,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY COALESCE(regime_source, 'PRE_MIGRATION')), 2) AS pct_within_source
FROM trading_bot.decision_trace
WHERE regime IS NOT NULL
GROUP BY 1, 2
ORDER BY source, cnt DESC;


-- ─── 3. AVERAGE CONFIDENCE BY REGIME AND SOURCE ─────────────────────────────
-- Higher confidence from H4 indicates better classifier quality

SELECT
    COALESCE(regime_source, 'PRE_MIGRATION') AS source,
    regime,
    COUNT(*) AS n,
    ROUND(AVG(regime_confidence), 4) AS avg_confidence,
    ROUND(MIN(regime_confidence), 4) AS min_confidence,
    ROUND(MAX(regime_confidence), 4) AS max_confidence
FROM trading_bot.decision_trace
WHERE regime IS NOT NULL
GROUP BY 1, 2
ORDER BY source, avg_confidence DESC;


-- ─── 4. BEFORE VS AFTER MIGRATION COMPARISON ────────────────────────────────
-- Direct comparison: PRE_MIGRATION vs H4_MARKET_CONTEXT regime distributions

SELECT
    CASE
        WHEN regime_source IS NULL OR regime_source = '' THEN 'BEFORE'
        ELSE 'AFTER'
    END AS migration_phase,
    regime,
    COUNT(*) AS cnt,
    ROUND(AVG(regime_confidence), 4) AS avg_conf,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (
        PARTITION BY CASE WHEN regime_source IS NULL OR regime_source = '' THEN 'BEFORE' ELSE 'AFTER' END
    ), 2) AS pct
FROM trading_bot.decision_trace
WHERE regime IS NOT NULL
GROUP BY 1, 2
ORDER BY migration_phase, cnt DESC;


-- ─── 5. DAILY REGIME TIMELINE (post-migration only) ─────────────────────────
-- Track how regime evolves day by day after migration

SELECT
    SUBSTR(timestamp_utc, 1, 10) AS date,
    symbol,
    regime,
    COALESCE(regime_source, 'PRE_MIGRATION') AS source,
    COUNT(*) AS decisions,
    ROUND(AVG(regime_confidence), 4) AS avg_confidence
FROM trading_bot.decision_trace
WHERE regime_source IS NOT NULL
  AND regime_source != ''
GROUP BY 1, 2, 3, 4
ORDER BY date, symbol, decisions DESC;


-- ─── 6. H4 AUTHORITY RATE PER SYMBOL ────────────────────────────────────────
-- Shows whether any symbol is falling back to M5 more than expected

SELECT
    symbol,
    SUM(CASE WHEN regime_source = 'H4_MARKET_CONTEXT' THEN 1 ELSE 0 END) AS h4_count,
    SUM(CASE WHEN regime_source = 'M5_CLASSIFIER' THEN 1 ELSE 0 END) AS m5_fallback_count,
    COUNT(*) AS total_post_migration,
    ROUND(
        SUM(CASE WHEN regime_source = 'H4_MARKET_CONTEXT' THEN 1 ELSE 0 END) * 100.0
        / NULLIF(COUNT(*), 0), 1
    ) AS h4_authority_pct
FROM trading_bot.decision_trace
WHERE regime_source IS NOT NULL
  AND regime_source != ''
GROUP BY symbol
ORDER BY h4_authority_pct;


-- ─── 7. REGIME × TERMINAL STAGE (post-migration) ────────────────────────────
-- Does H4 regime predict where decisions stop in the pipeline?

SELECT
    regime,
    terminal_stage,
    COUNT(*) AS cnt,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY regime), 2) AS pct_within_regime
FROM trading_bot.decision_trace
WHERE regime_source = 'H4_MARKET_CONTEXT'
  AND regime IS NOT NULL
  AND terminal_stage IS NOT NULL
GROUP BY regime, terminal_stage
ORDER BY regime, cnt DESC;


-- ─── 8. REGIME CONFIDENCE DISTRIBUTION BUCKETS ──────────────────────────────
-- Shows confidence quality for H4 vs M5 sources

SELECT
    COALESCE(regime_source, 'PRE_MIGRATION') AS source,
    CASE
        WHEN regime_confidence < 0.3 THEN 'LOW (0-0.3)'
        WHEN regime_confidence < 0.6 THEN 'MED (0.3-0.6)'
        WHEN regime_confidence < 0.8 THEN 'HIGH (0.6-0.8)'
        ELSE 'VERY_HIGH (0.8-1.0)'
    END AS confidence_bucket,
    COUNT(*) AS cnt,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (PARTITION BY COALESCE(regime_source, 'PRE_MIGRATION')), 2) AS pct
FROM trading_bot.decision_trace
WHERE regime_confidence IS NOT NULL
GROUP BY 1, 2
ORDER BY source, confidence_bucket;
