# MT5 account environments: phase 1

**Operational follow-up (2026-09-09):** see
[terminal separation report](mt5_accounts_operational_report.md) and
[concurrent verification](mt5_accounts_separation_verification.json).
Three portable installations now exist; MetaQuotes and Vantage verify, while
Admirals authentication blocks the A-C gate. The observations and remaining-work
list below describe the earlier phase-1 state, not the current terminal inventory.

Implemented: isolated read-only account targets and broker-eligibility previews.
MetaQuotes remains the default/baseline configuration. Admirals and Vantage are
additional observe-only targets, disabled by default. No production execution
hook, account switching, additional strategy engine, or S3 writer was added.

**Live setup is not yet a verified three-account deployment.** Only one running
terminal and installation was found, at
`C:\Program Files\MetaTrader 5\terminal64.exe`. A read-only account-info query
and the new worker both verified that this terminal is currently connected to
**Vantage**, not MetaQuotes. The existing bot was not restarted or reconfigured;
its process attachment was not inspected. Do not assume that a terminal folder
named MetaTrader 5 identifies its currently logged-in broker.

## Files changed

| File | Purpose |
| --- | --- |
| `.env.example` | Empty account configuration/credential-variable examples; additional targets disabled. |
| `core/accounts/__init__.py` | Independent account package; no runtime startup hooks. |
| `core/accounts/config.py` | Immutable configurations, account-local validation, reserved baseline and duplicate terminal/account rejection. |
| `core/accounts/identity.py` | One canonical request, multiple disabled execution targets, account-scoped execution/trade IDs. |
| `core/accounts/eligibility.py` | Read-only checks of permissions, symbol/volume/price/stop/freeze constraints and free margin; never resizes. |
| `core/accounts/terminal.py` | Existing-terminal inventory, hidden subprocess flags, OS-level worker leases. |
| `core/accounts/worker.py` | Single-account MT5 attachment, identity guards, state/symbol/exposure snapshots, optional margin preview. |
| `core/accounts/manager.py` | Concurrent isolated subprocesses, timeouts, response identity checks, fault isolation. |
| `core/accounts/__main__.py` | Diagnostic CLI and full JSON output. |
| `core/symbol_resolver.py` | Added `AccountSymbolResolver`; all existing code is unchanged. |
| `tests/_account_fake_mt5.py` | Test-only MT5 double and isolated subprocess fixture. |
| `tests/test_mt5_account_isolation.py` | Focused configuration, isolation, failure, identity and eligibility tests. |
| `docs/mt5_accounts_live_observation.json` | Redacted-to-account/spec-fields live Vantage worker result; no passwords, positions or deal details. |
| `docs/mt5_account_environments.md` | This trace, operational instructions, verification and limitations. |

## Current before-state: active path

1. `main.py:main` validates/freezes configuration and acquires the global runtime
   instance lock. With `MT5_CENTRALISED_INIT=True`, it initializes the configured
   terminal path, falling back to automatic terminal discovery. It supplies no
   login/server pin and does not call `mt5.login`.
2. `core/mt5_validation.py:validate_account` reads `terminal_info` and
   `account_info`, checking connection/trading permission/accessibility. It logs
   the account but does not verify that login/server belongs to MetaQuotes.
3. `core/loop.py` exposes `core/runtime/live_scanner.py:run_live_scanner`.
   `core/runtime/scanner_init.py:initialize_symbol_states` creates per-symbol
   `MT5DataFeed`, risk and trade-manager objects. With the explicit symbol list
   passed by `main`, the feed resolves individual symbols. Its
   `data/mt5_data.py:MT5DataFeed.resolve_symbol` tries exact/case-insensitive
   matches, then delegates to `core/symbol_resolver.py:resolve_broker_symbol`.
   The scanner's optional all-symbol resolution uses `resolve_all`.
4. `MT5DataFeed.last_tick` reads `symbol_info_tick`; `copy_rates_closed` calls
   `copy_rates_from_pos`. The existing bar provider and timestamp/collection
   path remain the sole canonical market source and are unchanged.
5. The scanner calls `core/v10/scanner_adapter.py:run_v10_cycle`.
   `core/runtime/account_provider.py:get_account_context` reads account_info;
   `get_broker_context` reads terminal, symbol and account metadata. The V10
   risk/execution engines consume these values. The existing execution-engine
   margin gate checks available margin is positive; it does not calculate
   proposed order margin. Runtime drawdown/daily-loss/exposure guards also
   consult the same global MT5 account/session.
6. `scanner_adapter._build_order_intent` translates the approved V10 result.
   `core/runtime/engine_execution_handler.py:prepare_execution` carries that
   intent and lineage into the scanner's execution path. After runtime guards,
   `execution/execution_orchestrator.py:ExecutionOrchestrator.execute_trade`
   calls `execution/mt5_execution.py:MT5Execution.execute` -> `place_market`.
7. `place_market` resolves the broker symbol, reads the tick/spec, validates
   volume/stops/spread, normalizes prices, constructs the MT5 request, and calls
   `mt5.order_send`. The same module handles execution results and retry logic;
   the scanner/orchestrator and existing persistence writers consume the result.
8. `core/trade_management/manager.py:TradeStateManager.register_from_execution`
   creates `pos_{deal}` (UUID fallback when no deal) and tracks positions by ID.
   Tick updates drive management. Stop modifications and partial/full closes
   call `MT5Execution.position_modify_sl_tp` / `close_position`; these query
   positions by ticket and send SLTP/closing requests through the same session.
9. `TradeStateManager._query_broker_close_history` queries `history_deals_get`
   by position identity. The existing lifecycle listeners and trade journal
   carry the close result into trade_journal/trade_truth. Recovery and
   reconciliation likewise query the global session's positions/deals.

The global-account assumptions found are:

| Area | Existing assumption |
| --- | --- |
| Startup/reconnect | `main`, `MT5DataFeed.connect`, `attempt_reconnect` own/reuse one default session; no expected login/server check. |
| Context/risk | Account/broker providers and risk guards query module-global MT5; balances/margin/exposure have no target parameter. |
| Symbols/specifications | `_resolved_symbols` is keyed by canonical symbol only; `_validated_specs` in mt5_execution is keyed by broker symbol only. |
| Market data | Feed broker name, broker-time offsets and caches assume the single market-source session. |
| Execution | `_recent_intents`, `_execution_metrics`, requests and results are process-local but not account-keyed. |
| Positions | Per-symbol TradeStateManager `_by_id`, retry queues, magic ownership and numeric MT5 tickets assume one account namespace. |
| Durable state | Drawdown/daily-loss state, journal `_persisted_ids`, recovery/checkpoint paths and account-derived runtime metrics assume one runtime account. |
| Collection | Execution datasets generally partition by schema/symbol/date; account identity is not a uniform identity component across execution/close records. |
| Lifecycle | One runtime lock, heartbeat and shutdown owner; starting three copies of main would also duplicate strategy/collection work. |

These legacy structures remain exclusive to the baseline runtime. Additional
workers never import the execution engine, legacy risk guards, trade managers,
bar collection or persistence writers, and never populate their caches.

## New account architecture and terminal model

Each `AccountConfig` represents account_id, broker, server, login, terminal_path,
enabled and role, plus an explicit per-account symbol map. It stores a password
environment-variable name, never a password. The repository's environment and
optional `.env` convention is retained. In phase 1 the terminal must already be
logged in; workers do not read the password variables or perform login changes.

Use three separate terminal installation directories and three distinct MT5
data directories, with one demo account logged into each. The production
MetaQuotes bot stays in its existing process. Diagnostics create one short-lived
Python process per enabled account, concurrently; these are readers, not three
strategy engines. They attach to the specified already-running terminal only.
No fallback terminal, symbol_select, order_send, or generic remote MT5 operation
is exposed. Shutdown disconnects only the worker's IPC connection.

This design follows the documented terminal-scoped API: omitted login uses the
last account, and initialization can launch a terminal. Requiring a visible
running executable avoids intentional terminal launches; an external terminal
exit racing initialization remains an operational limitation of the MT5 API.
See [MetaQuotes initialize documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py).

Account/session isolation controls:

- Explicit expected login/server, demo-mode and installation-path validation
  before any data read; checks repeat before and after each MT5 operation and
  before returning the snapshot. An identity change discards the entire result.
- Fixed account ownership for the worker lifetime, without login switching.
  Returned balances, positions, orders, deals, exposure, symbols and previews
  belong to that account. Unknown positions/orders never become zero exposure.
- Duplicate installation paths, duplicate server/login identities and shared
  terminal data directories are rejected. The configured baseline terminal and
  account are reserved even if baseline diagnostics are disabled.
- Per-terminal/data-directory OS leases prevent overlapping diagnostic owners.
  These locks do not control the existing bot or prevent a human from switching
  an MT5 terminal; operators must keep dedicated terminals pinned to their accounts.
- Parent-side response identity checks, account-local exceptions and bounded
  subprocess timeouts. A timeout kills only that Python worker, not MT5 or the bot.
- No shared account/spec/ticket/risk cache; snapshots are fresh. Secrets are not
  placed in subprocess arguments, request JSON or printed exception messages.

## Usage

Fill the non-secret pins and distinct terminal paths in environment variables or
the untracked `.env`, using the examples in `.env.example`. Set
`MT5_ADMIRALS_ENABLED=true` / `MT5_VANTAGE_ENABLED=true` only once their separate
terminals are running and logged into the expected demo accounts. Their role
remains `observe_only`; this flag does not enable trading.

```powershell
.venv/Scripts/python.exe -m core.accounts --config-only
.venv/Scripts/python.exe -m core.accounts
.venv/Scripts/python.exe -m core.accounts --json
.venv/Scripts/python.exe -m core.accounts --request existing_canonical_request.json --json
```

`--config-only` makes no MT5 connection. Normal output includes all ten symbols
for each account, even when disabled/unconfigured. JSON includes account margin,
positions/orders, a bounded preceding-24-hour deal snapshot, exposure and specs.
Broker `time`/`time_msc` in those diagnostic rows remain raw broker fields; they
are not canonical events or additions to the V1 market collection pipeline.

Request JSON contains `canonical_opportunity_id`, `correlation_id`, `decision_id`,
`symbol`, `side` (BUY/SELL), `volume`, `entry_price`, `sl`, `tp`. Supply a canonical
request already produced by the strategy; the command does not create signals.
No price or volume is changed. The preview checks account/terminal permissions,
symbol availability/trade direction, complete numeric specs, volume bounds/step,
price digits/tick grid, stop direction/distance, conservative freeze distance,
and account-local `order_calc_margin` against that account's free margin.

`broker_eligible` means only this preliminary preview passed. `execution_enabled`
is always false, including for baseline diagnostic targets. Margin calculation
does not guarantee broker acceptance or replace runtime exposure/drawdown guards;
see [MetaQuotes margin calculation documentation](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py).
No order is sent. A blocked account does not block sibling previews.

The CLI exits nonzero for enabled accounts with connection/state errors, missing
or ambiguous instruments, or blocked requested eligibility. A valid config-only
check exits zero without claiming connectivity.

## Verified live account state and symbol matrix

The new worker's read-only observation was captured at
**2026-09-09 17:20:58 UTC**, in `mt5_accounts_live_observation.json`.
It was a single-terminal observation, not a configured three-terminal deployment.
No login/server values were inferred for accounts that could not be checked.

| Field | METAQUOTES | ADMIRALS | VANTAGE (observed terminal) |
| --- | --- | --- | --- |
| Connection | Unverified | Unverified | Connected, identity verified |
| Login | Not supplied | Not supplied | 11041444 |
| Server | Not supplied | Not supplied | VantageGlobalPrimeLLP-Demo |
| Balance / equity | Unknown | Unknown | 100000 / 100000 |
| Margin / free margin / margin level | Unknown | Unknown | 0 / 100000 / 0 |
| Currency / leverage | Unknown | Unknown | USD / 30 |
| trade_allowed / trade_expert | Unknown | Unknown | true / true |
| Terminal trade_allowed / API disabled | Unknown | Unknown | true / false |

MetaQuotes/Admirals have no verified live specs for any instrument. The current
project account configuration is also unpopulated: MetaQuotes lacks login/server
pins; Admirals/Vantage are disabled and lack configured pins/paths. The Vantage
observation used an explicitly pinned one-off reader on the existing terminal.

| Canonical | METAQUOTES | ADMIRALS | VANTAGE strict resolution |
| --- | --- | --- | --- |
| EURUSD | Unverified | Unverified | EURUSD: available |
| GBPUSD | Unverified | Unverified | GBPUSD: available |
| USDJPY | Unverified | Unverified | USDJPY: available |
| USDCHF | Unverified | Unverified | USDCHF: available |
| USDCAD | Unverified | Unverified | USDCAD: available |
| AUDUSD | Unverified | Unverified | AUDUSD: available |
| NZDUSD | Unverified | Unverified | NZDUSD: available |
| NAS100 | Unverified | Unverified | Ambiguous: NAS100.i, NAS100ft.i |
| US500 | Unverified | Unverified | Unavailable under configured canonical/alias rules |
| XAUUSD | Unverified | Unverified | Ambiguous: XAUUSD, XAUUSD.crp |

Unavailable mapping does not prove the broker lacks an equivalent instrument.
Confirm the broker's intended contract and enter an explicit account-specific
map. Prefix/alias candidates are candidates only. The new resolver never uses
the legacy shortest-suffix or ordered-alias preference to break ambiguity.

Vantage resolved specifications (other accounts unknown):

| Symbol | Digits | Point | Contract size | Volume min / step / max | Stops / freeze (points) |
| --- | ---: | ---: | ---: | --- | --- |
| EURUSD | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| GBPUSD | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| USDJPY | 3 | 0.001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| USDCHF | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| USDCAD | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| AUDUSD | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| NZDUSD | 5 | 0.00001 | 100000 | 0.01 / 0.01 / 100 | 0 / 0 |
| NAS100 / US500 / XAUUSD | Unknown selected contract | Unknown | Unknown | Unknown | Unknown |

An earlier read-only candidate inspection found NAS100.i and NAS100ft.i both
require minimum/step 0.1 lots (digits 2, point .01, contract 1, maximum 500,
stops 400, freeze 0). A 0.01-lot request would fail those constraints; neither
contract was selected. XAUUSD and XAUUSD.crp have different specifications, so
the distinction must also be explicit rather than inferred from the shorter name.

## Lineage and sizing impact

No production dataset or schema version changed. The account layer persists no
execution rows; local JSON diagnostics are outside the canonical datasets.
`CanonicalExecutionRequest` is immutable, and multiple targets hold the same
parent request/lineage without rerunning strategy or duplicating opportunities.
Account execution IDs use UUID5 over account/broker/server/login plus parent
lineage. Account trade IDs include account/broker/server/login and broker ticket.
Equal tickets on different accounts cannot share these new scoped identities.
Existing baseline `pos_{deal}` IDs remain unchanged; the new IDs are not yet wired
into production order/close persistence because additional execution is disabled.

Assessment of future fan-out boundaries:

| Dataset | Required before parallel execution; no change in this patch |
| --- | --- |
| execution_context | Retain canonical parent; distinguish account snapshots from the existing once-per-cycle context. |
| execution_attempts / execution_results | Propagate account_id/broker/broker_server and scoped execution IDs on entry, retry, modify and close. |
| protection_audit / risk_deviation | Attach account identity and scoped execution/trade references to account-specific protection/risk truth. |
| management_actions | Account ownership and scoped ticket/trade identity through management and retry queues. |
| trade_truth / trade_journal | Account identity, scoped trade identity, account-local dedup/recovery and full parent lineage. |

Canonical events, market_context, opportunities, assessments, decision_trace,
decision_ledger, horizon_candidates and strategy_observations remain single-source
and account-independent. Future boundary integration must retain the V1 baseline;
it must not clone canonical rows or reuse numeric broker tickets as global IDs.

Strategy/entry/horizon/SL/TP/risk logic is unchanged. The legacy risk manager uses
`POSITION_SIZING_MODE=FIXED` and `FIXED_LOT=0.01`. However, the **active V10 path**
uses `risk_amount = account.balance * risk_pct`, then
`calculate_position_size_exact` using broker tick value/size and volume grid.
The scanner adapter forwards `order.volume` unchanged. It is therefore incorrect
to claim all current production opportunities are fixed 0.01 lots.
This patch introduces no new sizing algorithm and changes neither existing path.
A supplied 0.01-lot preview remains 0.01 for every account, with independent
eligibility; it is never scaled to account balance or rounded up to broker minimum.

## Tests

Focused command:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_mt5_account_isolation.py -p no:cacheprovider
```

Focused result: **35 passed**. Includes real separate Python worker processes
using fake MT5, independent account state and broker mappings, duplicate-account
and terminal rejection, mid-read identity changes, timeout/secret handling,
account-local failures and margin decisions, stable scoped IDs, strict symbol
ambiguity, legacy-default compatibility, and diagnostic CLI exit status.

Regression command:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_broker_portability.py tests/test_v10_account_provider.py tests/test_v10_broker_constraints.py tests/test_position_sizing.py tests/test_v10_risk_engine.py tests/test_v10_execution_engine.py tests/test_v10_execution_bridge.py tests/test_execution_layer.py tests/test_execution_orchestrator.py tests/test_execution_protection.py tests/test_execution_retry.py tests/test_trade_management_broker_close.py tests/test_trade_management_simulator.py tests/test_post_execution_handler.py tests/test_broker_side_close.py tests/test_v10_strategy_engine.py tests/test_patterns_unit.py tests/test_pattern_parity.py tests/test_strategy_framework.py tests/test_strategy_family.py tests/test_strategy_horizon_separation.py tests/test_horizon_execution_authority.py tests/test_decision_recorder.py tests/test_decision_ledger_integrity.py tests/test_execution_result_lineage_propagation.py tests/test_canonical_v1_contract.py -p no:cacheprovider
```

Regression result: **554 passed, 1 skipped, 1 failed**. The failing existing
`test_execution_layer.py::TestClosePosition::test_close_position_success` supplies
position magic 1 while the ownership guard requires 713001. The same failure was
reproduced with the original HEAD symbol resolver loaded in memory and the new
resolver class removed. Neither execution nor ownership code was changed.
All selected strategy/pattern/horizon tests passed. AST comparison verified that
removing only the new resolver class makes the resolver identical to HEAD.
Tests used fake MT5, temporary local persistence and existing S3 isolation fixtures.

## Remaining work before parallel demo execution

1. Identify the intended MetaQuotes baseline login/server and resolve why the
   terminal at the current baseline path is on Vantage. This task did not change
   that account, inspect passwords, stop/restart the bot, or switch any login.
2. Provision/identify distinct MetaQuotes, Admirals and Vantage terminal
   installations/data directories. Log each into its expected demo account and
   supply non-secret pins and paths. Changing the existing production terminal
   account or process requires a separately controlled operational action.
3. Run the diagnostic for all three concurrently. Require all identity checks,
   distinct terminal/data directories, complete account state and explicit
   resolution/specification of all ten instruments. Resolve NAS100/XAUUSD
   ambiguity and US500 mapping on Vantage using verified contracts.
4. Review account-level eligibility on the same canonical requests, with each
   account's own margin/exposure. A successful preview is not order authorization.
5. Before adding order sends, integrate a single canonical decision distributor
   with dedicated execution/management workers; account-scope durable risk state,
   queues, recovery, ticket ownership, IDs and all execution-boundary persistence
   listed above. Retain one strategy/market collection pipeline and V1 schemas.
   Add broker rejection/retry/restart/partial-fill/close and account-loss tests.
6. Enable controlled parallel demo execution only after those integrations and
   live isolation evidence are approved. Merely setting ENABLED=true in this
   phase cannot activate additional trading.

Production S3 mutations: **none**. Production bot restarts/reconfiguration:
**none**. Terminal logins switched/orders sent/Market Watch changes: **none**.
