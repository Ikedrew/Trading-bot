# MT5 terminal separation: operational follow-up, 2026-09-09

**Partial completion: three isolated terminals prepared; two accounts verified.
Admirals authentication blocks Phase B. Runtime integration (D-H) has not begun,
as explicitly required until A-C pass. No account is execution-ready through the
new account layer.**

## 1. Terminal architecture

| Account / broker | Executable | Prepared data directory |
| --- | --- | --- |
| METAQUOTES / MetaQuotes | `C:\MT5Accounts\METAQUOTES\terminal64.exe` | `C:\MT5Accounts\METAQUOTES` |
| ADMIRALS / Admirals | `C:\MT5Accounts\ADMIRALS\terminal64.exe` | `C:\MT5Accounts\ADMIRALS` |
| VANTAGE / Vantage | `C:\MT5Accounts\VANTAGE\terminal64.exe` | `C:\MT5Accounts\VANTAGE` |

All three processes run concurrently in portable mode. The data-directory pins
were verified through MT5 for MetaQuotes and Vantage; Admirals cannot yet return
authenticated terminal/account information. MetaQuotes uses its existing demo
account, not a newly created or replacement account.

The original `C:\Program Files\MetaTrader 5\terminal64.exe` remains running
(PID 5648, start time 2026-09-09 10:28:36). Its original data directory is
`C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075`.
Its profiles, history, Market Watch, saved logins and process were not modified.
The new terminal PIDs observed were METAQUOTES 2868, ADMIRALS 212, VANTAGE 4252.
PIDs are observations, not configuration identities.

The preparation script copied the existing signed executable and opaque saved
account/server databases into private directories (SYSTEM/Administrators ACL).
No password was decoded, printed, hardcoded or copied into the repository.
No source profiles, charts or expert advisers were copied. Each bootstrap pins
one login/server and disables expert/live trading. The executable SHA256 was
`3DBB5F966441FC043D49BC637A5931686BE847E77DC89E295C944EDA6B9A08C7`.
The local manifest is `C:\MT5Accounts\manifest.json`.

Separate installation directories and writable portable data directories follow
the [MT5 startup documentation](https://www.metatrader5.com/en/terminal/help/start_advanced/start).
Do not rerun the preparation script against these existing directories: it
deliberately refuses to replace or merge them.

Ignored `.env` now pins `MT5_{ACCOUNT}_LOGIN`, `SERVER`, `TERMINAL_PATH`,
`TERMINAL_DATA_PATH`, `PORTABLE=true`, `ENABLED=true` for all three account
diagnostics. METAQUOTES retains role `baseline`; additional roles are
`observe_only`. Explicit maps for the verified accounts are also stored there.
These new account-layer variables do not redirect the existing main.py runtime.

Workers attach by executable path to an already-running terminal, with portable
mode explicit. Expected login, exact server, demo status, executable directory
and configured data directory must match before any symbol/position reads.
Identity is rechecked around every MT5 read; there is no login call or fallback
attachment. Each account has a separate Python subprocess and MT5 terminal.

## 2. Account verification

The [final concurrent diagnostic artifact](mt5_accounts_separation_verification.json)
contains start/end UTC timestamps, worker PIDs, actual paths, all account fields
and thirty symbol rows. Trade details are reduced to counts.

| Field | METAQUOTES | ADMIRALS | VANTAGE |
| --- | --- | --- | --- |
| Result | VERIFIED | ERROR: INITIALIZE_FAILED | VERIFIED |
| Login | 5055599469 | 42890920 (configured, unverified) | 11041444 |
| Server | MetaQuotes-Demo | AdmiralsGroup-Demo (configured, unverified) | VantageGlobalPrimeLLP-Demo |
| Balance / equity | 9715.90 / 9715.90 | unknown | 100000 / 100000 |
| Margin / free margin | 0 / 9715.90 | unknown | 0 / 100000 |
| Margin level | 0 | unknown | 0 |
| Currency | GBP | unknown | USD |
| Leverage | 100 | unknown | 30 |
| Account trade_allowed / trade_expert | true / true | unknown | true / true |
| Terminal trade_allowed | false | unknown | false |
| Open positions / orders | 0 / 0 | unknown | 0 / 0 |

The new Admirals terminal log reports authorization failed: **invalid account**
for 42890920 / AdmiralsGroup-Demo. These identifiers came from a local log record;
they are not a verified Admirals identity. The user has been asked to confirm
the exact demo login/server or authenticate the separate Admirals terminal
directly. Do not send passwords in chat, guess another server, or switch the
original production terminal. The other account diagnostics finish successfully
despite this error.

## 3. Symbol matrix

| Canonical | METAQUOTES | ADMIRALS | VANTAGE |
| --- | --- | --- | --- |
| EURUSD | EURUSD | not checked: authentication blocked | EURUSD |
| GBPUSD | GBPUSD | not checked: authentication blocked | GBPUSD |
| USDJPY | USDJPY | not checked: authentication blocked | USDJPY |
| USDCHF | USDCHF | not checked: authentication blocked | USDCHF |
| USDCAD | USDCAD | not checked: authentication blocked | USDCAD |
| AUDUSD | AUDUSD | not checked: authentication blocked | AUDUSD |
| NZDUSD | NZDUSD | not checked: authentication blocked | NZDUSD |
| NAS100 | USTEC: trading disabled | not checked: authentication blocked | NAS100.i |
| US500 | US500: trading disabled | not checked: authentication blocked | SP500.i |
| XAUUSD | XAUUSD | not checked: authentication blocked | XAUUSD |

All twenty selected contracts are present with valid specifications. The
diagnostic status `available` means a symbol/specification was resolved, not
permission to execute. **MetaQuotes USTEC and US500 have trade_mode=0** and must
remain blocked for trading. All other selected contracts have trade_mode=4.
Admirals rows are unverified, not evidence that the broker lacks instruments.

Relevant numeric specifications (stops/freeze are broker points):

| Account / instrument | Digits / point | Contract size | Volume min / step / max | Stops / freeze |
| --- | --- | --- | --- | --- |
| MQ FX except USDJPY | 5 / 0.00001 | 100000 | 0.01 / 0.01 / 500 | 0 / 0 |
| MQ USDJPY | 3 / 0.001 | 100000 | 0.01 / 0.01 / 500 | 0 / 0 |
| MQ USTEC, US500 | 2 / 0.01 | 1 | 0.1 / 0.1 / 250 | 0 / 0 |
| MQ XAUUSD | 2 / 0.01 | 100 | 0.01 / 0.01 / 100 | 0 / 0 |
| Vantage FX except USDJPY | 5 / 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| Vantage USDJPY | 3 / 0.001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| Vantage NAS100.i | 2 / 0.01 | 1 | 0.1 / 0.1 / 500 | 400 / 0 |
| Vantage SP500.i | 2 / 0.01 | 1 | 0.1 / 0.1 / 500 | 200 / 0 |
| Vantage XAUUSD | 2 / 0.01 | 100 | 0.01 / 0.01 / 100 | 20 / 0 |

The [mapping evidence](mt5_accounts_symbol_mapping_evidence.json) includes
description, path, trade mode, currencies, contract size, precision, volume,
stop/freeze, calculation mode and expiry for selected and rejected alternatives.
The full catalog is retained privately at
`C:\MT5Accounts\symbol_inventory_20260909.json` (MQ 12411; Vantage 924 symbols).

Mapping decisions:

- Vantage `NAS100.i` is described as **NAS100 Cash**; `NAS100ft.i` is
  **NAS100 Future**. `SP500.i` is **S&P Index Cash CFD (USD)**;
  `SP500ft.i` is **SP500 Future**. All are under `CFDs-index` and USD/USD.
- Vantage `XAUUSD` is **Gold US Dollar**, under `Gold`, XAU/USD, contract 100.
  `XAUUSD.crp` explicitly says **Not for trading purposes, currency conversion
  displays only**, is under `Forex Major`, and has trade_mode=0.
- MQ `USTEC` is **US Tech 100 Index**, and `US500` is **US SPX 500 Index**,
  under `Indexes`, USD/USD. The separately listed `USTECH100M` and `US500M`
  are mini variants with different precision/volume constraints. Mapping the
  standard contracts does not override their disabled trading status.
- FX exact names and the MQ metal description/currencies identify the intended
  canonical instruments. Each explicit map is scoped to its account; the
  legacy resolver and canonical identifiers are unchanged.

## 4. Runtime integration

**Not performed: Phase B has failed and Phase C is incomplete.** The active
runtime remains the single-session path documented in
[the phase-1 trace](mt5_account_environments.md#current-before-state-active-path):
`main.main -> run_live_scanner -> scanner_adapter.run_v10_cycle ->
_build_order_intent -> prepare_execution -> ExecutionOrchestrator.execute_trade
-> MT5Execution.execute -> place_market -> mt5.order_send`.

There is no new runtime fan-out point. The implemented read-only path is
`core.accounts.__main__.main -> manager.diagnose -> run_isolated -> worker.run_worker
-> AccountReader.verify/snapshot`. It runs no strategy or canonical persistence.

## 5. Account eligibility

Existing phase-1 previews use each worker's account balance/equity, permissions,
broker symbol/specification, tick, exposure and order_calc_margin response.
They validate the supplied volume, price grid, stop/freeze distances and free
margin independently. They are not integrated V10 risk authorization.

The active V10 production path sizes from account balance/risk percentage and
broker tick metadata; the legacy path uses fixed 0.01. Neither was changed.
Future integration must obtain each target's own state before calling the
existing sizing logic and must reject a below-minimum risk-safe size instead of
rounding it up. No balance normalization or cross-account volume reuse was added.

## 6. Execution isolation

The new workers are read-only and cannot dispatch order_send through their IPC
allowlist. Existing phase-1 target records have execution_enabled=false. No new
account receives production orders. Account-specific order submission, retry and
result routing remain Phase E work after the isolation gates pass.

## 7. Position-management isolation

Diagnostic positions/orders/deals carry account_id, broker, broker_server,
login and a scoped ID. No account worker invokes management. Active management
and restart recovery still use the original single-session runtime and have not
been extended. Production ticket ownership, recovery, protection, close and
history routing must be integrated and tested before additional execution.

## 8. V1 lineage

No canonical IDs or persisted execution/trade identities changed. The existing
phase-1 identity helper can describe three disabled targets sharing a single
CanonicalExecutionRequest; this is not live fan-out. For an illustrative parent
`decision_id=decision-demo`, target IDs include the account ID and a UUID5 of the
account/broker/server/login plus shared lineage. Trade IDs similarly include
account identity plus ticket, so equal numeric tickets across brokers are
distinct. No such example was persisted or executed.

Illustration generated with the existing helper (shared opportunity-demo,
correlation-demo, decision-demo; hypothetical ticket 12345 on every account):

| Account | Execution ID | Trade ID |
| --- | --- | --- |
| METAQUOTES | execution_METAQUOTES_74b0c55af51151eaa5dd13f6b24d1225 | trade_METAQUOTES_32d68840cd0d55bd9c9b00da068a740a |
| ADMIRALS | execution_ADMIRALS_c9dd6415cade5f27838c6d89566074c1 | trade_ADMIRALS_3381cc9668fb528894effadd4ca5918a |
| VANTAGE | execution_VANTAGE_78caf23658945ebaa69e4f86a538add7 | trade_VANTAGE_3be244afb9ad506d9e386cde1ccad902 |

## 9. Persistence and files changed

No changes to execution_context, execution_attempts, execution_results,
protection_audit, risk_deviation, management_actions, trade_truth or trade_journal.
Their account-aware execution/recovery changes remain gated work. All schemas
remain V1. No S3 data was written, migrated, cleaned or deleted.

This continuation changed:

| File | Purpose |
| --- | --- |
| `core/accounts/config.py` | Optional expected terminal-data path and portable configuration/validation. |
| `core/accounts/worker.py` | Fail closed on wrong configured data directory; portable attachment; descriptive contract metadata and optional full inventory. |
| `core/accounts/manager.py` | Propagate optional inventory request to independent workers. |
| `core/accounts/__main__.py` | Add read-only --symbol-inventory option. |
| `.env.example` | Document per-account data-path and portable variables. |
| `.env` (ignored) | Non-secret local terminal/login/server pins and verified MQ/Vantage explicit symbol maps. |
| `tools/prepare_mt5_account_terminals.ps1` | VM-specific one-time signed portable setup; private ACL, no replacement, trading disabled. |
| `tests/test_mt5_account_terminal_separation.py` | Five additional terminal-pin/inventory regressions. |
| `docs/mt5_account_environments.md` | Point earlier observations to this operational follow-up. |
| `docs/mt5_accounts_operational_report.md` | Current results, gates, specifications and continuation instructions. |
| `docs/mt5_accounts_separation_verification.json` | Final concurrent redacted diagnostic. |
| `docs/mt5_accounts_symbol_mapping_evidence.json` | Selected/rejected contract metadata supporting explicit maps. |

Other phase-1 files shown as untracked by git predate this continuation; they
were retained. No strategy/execution/persistence production modules were edited.

## 10. Trading logic

Strategy, pattern, opportunity, entry, horizon, sizing methodology and SL/TP
logic are unchanged. No second or third strategy engine was started. The
production bot was not stopped, restarted or reconfigured. Its terminal was not
switched. Only the new isolated read-only terminals were created/started.

## 11. Tests

Focused, first:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_mt5_account_isolation.py tests/test_mt5_account_terminal_separation.py -p no:cacheprovider
```

**40 passed** (original 35 retained, five added). New tests cover portable/env
pins, wrong data directory before reads, explicit portable initialization with
no login, descriptive cash/future inventory with strict ambiguity, and all ten
unverified rows preserving metadata columns. Existing tests cover account-local
failure/state/margin, subprocess isolation, scoped IDs and baseline compatibility.

Regression, second:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_broker_portability.py tests/test_v10_account_provider.py tests/test_v10_broker_constraints.py tests/test_position_sizing.py tests/test_v10_risk_engine.py tests/test_v10_execution_engine.py tests/test_v10_execution_bridge.py tests/test_execution_layer.py tests/test_execution_orchestrator.py tests/test_execution_protection.py tests/test_execution_retry.py tests/test_trade_management_broker_close.py tests/test_trade_management_simulator.py tests/test_post_execution_handler.py tests/test_broker_side_close.py tests/test_v10_strategy_engine.py tests/test_patterns_unit.py tests/test_pattern_parity.py tests/test_strategy_framework.py tests/test_strategy_family.py tests/test_strategy_horizon_separation.py tests/test_horizon_execution_authority.py tests/test_decision_recorder.py tests/test_decision_ledger_integrity.py tests/test_execution_result_lineage_propagation.py tests/test_canonical_v1_contract.py -p no:cacheprovider
```

**554 passed, 1 skipped, 1 failed in 27.44s.** The failure remains
`TestClosePosition.test_close_position_success`: fixture magic=1 is rejected by
the existing ownership guard requiring 713001 (`OWNERSHIP_VIOLATION`). The prior
phase reproduced this failure against the original resolver. No ownership or
strategy tests were edited to suppress it. Selected strategy/pattern/horizon and
V1 tests passed. Execution/recovery fan-out acceptance tests remain unimplemented
because their corresponding runtime changes are explicitly gated.

Final live check (same command path, redacted artifact saved separately):

```powershell
.venv/Scripts/python.exe -m core.accounts --json --timeout 40
```

The diagnostic is expected to return nonzero until Admirals verifies. To inspect
full broker descriptions after fixing that terminal, add `--symbol-inventory`.
Neither command sends trades or switches logins.

## 12. Deployment status and remaining work

| Account | Current new-layer status | Execution ready |
| --- | --- | --- |
| METAQUOTES | CONNECTED / OBSERVE_ONLY diagnostics | No |
| ADMIRALS | BLOCKED: invalid account authentication | No |
| VANTAGE | CONNECTED / OBSERVE_ONLY diagnostics | No |

The unchanged production execution path is separate from these diagnostic
statuses. These results do not attest to its current live account attachment.

1. Authenticate the existing Admirals demo in its new dedicated terminal using
   the correct login/server. Update its non-secret pins/bootstrap/manifest if
   identifiers differ. Never supply a password in the repository or chat.
2. Repeat concurrent verification; inspect Admirals contract descriptions and
   pin explicit mappings or record genuinely unavailable instruments. Require
   correct actual data directory and demo/login/server identity for all three.
3. Keep MQ disabled index contracts blocked. Investigate broker availability
   without substituting an unrelated instrument or changing canonical identity.
4. Only after A-C pass, implement D-H: one decision distributor, existing V10
   per-account risk sizing, worker execution, durable account-owned management
   and recovery, V1 boundary persistence, and independent promotion switches.
5. Add the requested execution/management/restart/fault-isolation tests and
   resolve the separately documented regression fixture issue before claiming
   a fully passing deployment suite. Prove all execution-readiness conditions.
6. A future production runtime deployment will require a controlled bot restart
   and deliberate transition to the pinned MetaQuotes worker. Report that
   requirement before stopping the bot; no restart was performed in this task.

**Do not enable parallel trading from this partial operational result.**
