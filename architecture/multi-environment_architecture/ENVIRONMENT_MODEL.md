# Environment Model Specification

## Version: 1.0
## Date: 2026-07-23

---

## 1. Definition

An **Environment** is an independent trading execution context that:
- Receives OpportunityAssessments from the shared intelligence engine
- Applies its own policy rules to accept/reject opportunities
- Manages its own risk state (positions, exposure, drawdown)
- Executes through its own broker session
- Persists its own trade lifecycle records
- Reports its own performance metrics

---

## 2. Environment Identity

```python
@dataclass(frozen=True)
class EnvironmentIdentity:
    env_id: str           # "retail_growth_v1", "ftmo_v1"
    display_name: str     # "Retail Growth"
    account_type: str     # "RETAIL" | "PROP"
    broker: str           # "pepperstone" | "ftmo" | "the5ers"
    magic_number: int     # Unique per environment (position identification)
    profile_version: str  # "1.0.0" (semantic versioning)
    created_at: str       # ISO timestamp
```

---

## 3. Environment Responsibilities

| Responsibility | Description |
|---------------|-------------|
| **Policy evaluation** | Accept/reject opportunities based on profile rules |
| **Risk calculation** | SL/TP geometry + volume sizing from own balance |
| **Guard enforcement** | Daily limits, cooldowns, exposure caps |
| **Position ownership** | Track own open/closed positions by magic number |
| **Execution** | Place/modify/close orders through broker |
| **State persistence** | Maintain own equity curve, trade journal, drawdown |
| **Lifecycle management** | Startup recovery, graceful shutdown, state checkpoint |
| **Observability** | Emit decisions, events, alerts tagged with env_id |

---

## 4. Environment State

```python
@dataclass
class EnvironmentState:
    # Identity
    env_id: str
    status: EnvironmentStatus  # ACTIVE | PAUSED | STOPPED
    
    # Account state
    balance: float
    equity: float
    peak_equity: float
    current_drawdown_pct: float
    daily_pnl: float
    
    # Position state
    open_positions: list[Position]
    position_count: int
    total_exposure_lots: float
    
    # Trade state
    trades_today: int
    last_trade_time: float
    cooldown_remaining: float
    
    # Lifecycle
    started_at: float
    last_checkpoint: float
    total_trades: int
    total_wins: int
    win_rate: float
```

---

## 5. Environment Isolation Guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| Position isolation | Unique magic number per environment |
| State isolation | Separate TradeStateManager instance |
| Risk isolation | Independent drawdown/exposure tracking |
| Execution isolation | Cannot modify another env's positions |
| Persistence isolation | env_id partition key on all records |
| Failure isolation | Exception in one env doesn't crash others |

---

## 6. Environment Does NOT Own

| Shared Resource | Owner |
|-----------------|-------|
| Market data | Scanner (shared) |
| Pattern detection | Intelligence engine (shared) |
| OpportunityAssessment | Intelligence engine (shared, immutable) |
| Score calibration curve | Research engine (shared artifact) |
| MT5 terminal process | System (shared, environments share connection) |

---

## 7. Environment Lifecycle Events

| Event | Trigger | Action |
|-------|---------|--------|
| `ENV_CREATED` | Profile loaded at startup | Initialize state objects |
| `ENV_STARTED` | Broker connection established | Position recovery, checkpoint load |
| `ENV_OPPORTUNITY` | OpportunityAssessment received | Policy evaluation begins |
| `ENV_TRADE_OPENED` | Broker fill confirmed | Register position, update state |
| `ENV_TRADE_CLOSED` | Position exit detected | Journal, trade truth, update P&L |
| `ENV_PAUSED` | Operator command / rule trigger | Stop new entries, manage existing |
| `ENV_STOPPED` | Shutdown / config change | Checkpoint state, close connections |
| `ENV_ERROR` | Unrecoverable failure | Alert operator, isolate from others |

---

## 8. Inter-Environment Rules

| Scenario | Allowed? | Reason |
|----------|----------|--------|
| Two envs trade same symbol | Yes | Different magic numbers → independent positions |
| Two envs on same broker account | Yes | Magic number separates ownership |
| Env A blocks because of Env B's position | **No** | Environments are portfolio-independent |
| Shared correlation guard across envs | **No** | Each env owns its own exposure tracking |
| Global drawdown halt (all envs) | **Optional** | Configurable kill switch at platform level |

---

## 9. Example: Two Active Environments

```
Environment: retail_growth_v1
  Account: Pepperstone #12345
  Magic: 713001
  Balance: $5,000
  Policy: EV gate ON, session guard ON, min SL 5 pips
  Positions: 1 open (EURUSD SELL)
  Status: ACTIVE

Environment: ftmo_v1  
  Account: FTMO #67890
  Magic: 713002
  Balance: $100,000
  Policy: EV gate ON, daily loss 4%, max drawdown 8%, consistency rules
  Positions: 0 open
  Status: ACTIVE
  
Same opportunity arrives: AUDUSD SELL, score=0.72, EV depends on sizing

  retail_growth_v1: evaluates → ACCEPT (EV positive with 0.01 lot sizing)
  ftmo_v1: evaluates → ACCEPT (EV positive with 0.5 lot sizing)
  
Both place independent orders with different volumes and magic numbers.
```
