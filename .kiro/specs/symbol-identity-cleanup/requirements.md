# Symbol Identity Cleanup — Requirements

## Problem Statement

The system migrated to canonical symbols (EURUSD, GBPUSD, etc.) but legacy `_SB` suffix 
persists in internal identity, persistence keys, test fixtures, and default values. This 
causes inconsistent S3 partition names, ledger entries, and makes cross-system queries 
require handling two naming conventions.

## Goal

All internal/persistence layers use canonical symbol identity consistently:
- `events: symbol=EURUSD`
- `execution_context: symbol=EURUSD`
- `decision_ledger: symbol=EURUSD`
- `trade_truth: symbol=EURUSD`

Broker execution retains resolution: `EURUSD -> EURUSD_SB`

## Scope

### Must Change (Internal Identity)

1. **config.py** — `CORRELATION_GROUPS` (lines 165-166): use canonical names
2. **config.py** — `MAX_SPREAD_ABSOLUTE` keys: use canonical names  
3. **risk/correlation_guard.py** — `_DEFAULT_GROUPS` + `_PAIR_CURRENCIES`: canonical
4. **core/pipeline/event_observer.py** — `_SYMBOL_CHANNELS` keys: canonical
5. **Docstring examples** in ~15 core modules: update to canonical
6. **Test fixtures** (~50+ test files): update default symbol values
7. **risk/models.py** — OrderIntent docstring: clarify it holds EITHER format

### Must Keep (Broker Resolution)

1. **core/symbol_resolver.py** — resolution logic and output examples
2. **Defensive .replace("_SB", "")** code in correlation.py, slippage_monitor.py, 
   forensic_logger.py, correlation_validator.py
3. **core/audit_persistence.py** — directory pattern matching (existing data)
4. **config.py** — Discord webhook channel NAMES (cosmetic, low priority)

### Must NOT Change

1. Broker symbol resolution behaviour
2. Trading logic, decision logic, risk logic, execution behaviour
3. Live system's ability to process `_SB` suffixed symbols from MT5

## Migration Strategy

### Phase 1: Core Config + Risk Mapping (highest impact)
- config.CORRELATION_GROUPS
- config.MAX_SPREAD_ABSOLUTE  
- risk/correlation_guard.py defaults

### Phase 2: Internal Module Docstrings
- All `symbol="EURUSD_SB"` in docstrings/comments → `symbol="EURUSD"`

### Phase 3: Event/Persistence Layer
- event_observer.py channel mapping

### Phase 4: Test Fixtures (bulk — largest file count)
- Update all test files using `_SB` as default symbol values
- Tests should use canonical symbols for internal-facing tests
- Tests for broker-facing code (execution, spread guard) may keep `_SB`

## Verification

1. Full test suite passes after changes
2. No persistence path changes that break existing data reads
3. Defensive stripping code handles both formats gracefully

## Retained References Report

After cleanup, the following `_SB` references are intentionally retained:
- `core/symbol_resolver.py` — broker resolution output documentation
- `core/correlation.py` — defensive `.replace("_SB", "")` 
- `core/slippage_monitor.py` — defensive `.replace("_SB", "")`
- `core/pipeline/forensic_logger.py` — defensive `.replace("_sb", "")`
- `core/contracts/validators/correlation_validator.py` — defensive stripping
- `core/audit_persistence.py` — existing data directory pattern matching
- `config.py` Discord webhook channel names (cosmetic routing)
- `risk/models.py` — OrderIntent comment noting broker usage
