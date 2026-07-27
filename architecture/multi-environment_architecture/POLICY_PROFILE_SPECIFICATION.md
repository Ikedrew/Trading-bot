# Policy Profile Specification

## Version: 1.0
## Date: 2026-07-23

---

## 1. Structure

A **PolicyProfile** is a frozen configuration set that defines how an environment evaluates opportunities and manages risk. It is versioned, immutable once deployed, and composable from base profiles.

```yaml
profile:
  id: "retail_growth_v1"
  version: "1.0.0"
  base: null  # or "retail_base_v1" for inheritance
  account_type: "RETAIL"
  
execution_policy:
  ev_gate_enabled: true
  min_score_threshold: 0.35
  min_ev_threshold: 0.0
  session_guard_enabled: true
  trading_hours_start_utc: 7
  trading_hours_end_utc: 21

risk_parameters:
  position_sizing_mode: "DYNAMIC"
  risk_per_trade_percent: 1.0
  fixed_lot: 0.01
  min_rr: 2.0
  base_rr: 2.0
  sl_buffer: 0.0002
  min_sl_guard_enabled: true
  adaptive_min_sl_enabled: true
  min_sl_absolute_floor_pips: 3.0
  atr_sl_multiplier: 1.0
  spread_sl_multiplier: 2.0

exposure_limits:
  max_open_positions: 3
  max_total_risk_exposure_pct: 3.0
  max_currency_exposure_lots: 15.0
  correlation_guard_enabled: true
  max_correlation_group_positions: 2

trade_management:
  break_even_trigger_rr: 1.0
  break_even_buffer: 0.1
  trailing_step: 0.0
  trailing_start_rr: 0.0
  partial_tp_fraction: 0.0
  max_time_in_trade_seconds: 0

daily_limits:
  max_trades_per_day_total: 20
  max_trades_per_day_per_symbol: 5
  daily_loss_limit_percent: 8.0
  cooldown_seconds: 300
  cooldown_after_loss_seconds: 600

drawdown_protection:
  enable_drawdown_guard: false
  max_drawdown_percent: 10.0

prop_firm_rules:
  enabled: false
  
challenge_rules:
  enabled: false

consistency_rules:
  enabled: false

weekend_protection:
  flatten_before_weekend: true
  friday_flatten_hour_utc: 20
  block_new_trades_before_weekend: true
```

---

## 2. Example Profiles

### Retail Growth v1
```yaml
id: "retail_growth_v1"
account_type: "RETAIL"
execution_policy:
  ev_gate_enabled: false  # Experiment mode — collecting data
  session_guard_enabled: false
risk_parameters:
  position_sizing_mode: "DYNAMIC"
  risk_per_trade_percent: 1.0
exposure_limits:
  max_open_positions: 5
daily_limits:
  daily_loss_limit_percent: 8.0
drawdown_protection:
  enable_drawdown_guard: false
```

### Retail Conservative v1
```yaml
id: "retail_conservative_v1"
account_type: "RETAIL"
execution_policy:
  ev_gate_enabled: true
  session_guard_enabled: true
risk_parameters:
  position_sizing_mode: "DYNAMIC"
  risk_per_trade_percent: 0.5
exposure_limits:
  max_open_positions: 2
daily_limits:
  daily_loss_limit_percent: 4.0
drawdown_protection:
  enable_drawdown_guard: true
  max_drawdown_percent: 8.0
```

### FTMO v1
```yaml
id: "ftmo_v1"
account_type: "PROP"
execution_policy:
  ev_gate_enabled: true
  session_guard_enabled: true
  trading_hours_start_utc: 8
  trading_hours_end_utc: 20
risk_parameters:
  position_sizing_mode: "DYNAMIC"
  risk_per_trade_percent: 0.5
  min_rr: 2.0
exposure_limits:
  max_open_positions: 3
  max_total_risk_exposure_pct: 2.0
daily_limits:
  max_trades_per_day_total: 10
  daily_loss_limit_percent: 4.0
  cooldown_seconds: 600
drawdown_protection:
  enable_drawdown_guard: true
  max_drawdown_percent: 8.0
prop_firm_rules:
  enabled: true
  max_daily_loss_percent: 5.0
  max_total_drawdown_percent: 10.0
  max_lot_size: 5.0
  blocked_trading_hours: [[22, 24]]
  allow_weekend_holding: false
challenge_rules:
  enabled: true
  profit_target_percent: 8.0
consistency_rules:
  enabled: true
  max_daily_profit_percent: 2.0
  max_single_day_contribution_percent: 40.0
```

### The5ers v1
```yaml
id: "the5ers_v1"
account_type: "PROP"
execution_policy:
  ev_gate_enabled: true
  session_guard_enabled: true
risk_parameters:
  position_sizing_mode: "DYNAMIC"
  risk_per_trade_percent: 0.25
exposure_limits:
  max_open_positions: 2
daily_limits:
  daily_loss_limit_percent: 3.0
drawdown_protection:
  enable_drawdown_guard: true
  max_drawdown_percent: 4.0
prop_firm_rules:
  enabled: true
  max_daily_loss_percent: 4.0
  max_total_drawdown_percent: 6.0
  max_lot_size: 2.0
  allow_weekend_holding: false
```

---

## 3. Profile Versioning

Profiles are immutable once deployed. Changes create new versions:
- `retail_growth_v1` → `retail_growth_v2` (new version, both exist in history)
- All trades reference their profile version for forensic analysis
- Historical analysis can compare performance across profile versions

---

## 4. Profile Inheritance (Optional)

```yaml
# Base profile (shared defaults)
id: "prop_base_v1"
account_type: "PROP"
execution_policy:
  ev_gate_enabled: true
  session_guard_enabled: true
drawdown_protection:
  enable_drawdown_guard: true

# Derived profile (overrides only)
id: "ftmo_v1"
base: "prop_base_v1"
prop_firm_rules:
  max_daily_loss_percent: 5.0
  max_total_drawdown_percent: 10.0
```

Inheritance reduces duplication but increases complexity. Recommended only when 5+ profiles share a base.

---

## 5. Adding New Prop Firms

To add a new prop firm:
1. Create a new YAML profile with the firm's rules
2. Assign a unique `env_id` and `magic_number`
3. Configure broker connection credentials
4. Deploy — no code changes required

The intelligence engine, scoring system, and pattern detection are completely unchanged.
