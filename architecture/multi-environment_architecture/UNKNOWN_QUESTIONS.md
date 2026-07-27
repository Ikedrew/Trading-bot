# Unknown Questions — Cannot Answer Today

## Date: 2026-07-23

These questions CANNOT be answered with current data or require additional evidence.

---

## Strategy & Edge (HIGH PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 1 | Does the strategy have positive expectancy? | Only 13 trades, all under compromised conditions | 50+ trades with corrected architecture |
| 2 | What is the true win rate? | 7.7% from a structurally flawed sample | 50+ clean trades |
| 3 | Which market regime produces profits? | All 13 trades in TRANSITIONAL | Trades across STRUCTURED + TRENDING + RANGING |
| 4 | Which session is most profitable? | All 13 trades off-session | Trades during London + NY sessions |
| 5 | Is the probability model calibrated? | 9 trades vs 27.8% predicted — within CI but too few | 100+ trades per calibration bucket |
| 6 | Does pattern quality predict outcomes? | Only 1 winner — cannot correlate | 30+ trades per pattern type |
| 7 | Does confirmation score predict success? | Single winner had high confirmation, but N=1 | Statistical sample |

## EV Gate (HIGH PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 8 | Does EV gate improve net profitability? | EV gate was OFF for all trades | 50 trades with EV experiment markers |
| 9 | Are EV-rejected trades actually losers? | Shadow trades exist but haven't been compared to outcomes | Cross-reference shadow R-multiples |
| 10 | What EV threshold maximises expectancy? | Need multiple threshold tests | Walk-forward optimisation on 200+ trades |
| 11 | Is there an optimal p_success floor? | Never tested | Parametric analysis |

## Entry Quality (MEDIUM PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 12 | How far does price move against us (MAE)? | MAE not tracked for live positions | Add max_adverse_price to Position |
| 13 | Are entries too early or too late? | No time-to-MFE metric | Track bar count from entry to MFE |
| 14 | Does entry timing correlate with score? | Requires score × timing × outcome dataset | 50+ trades with timing data |

## Exit Quality (MEDIUM PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 15 | What % of SL exits would have recovered? | No MAE tracking → cannot determine | MAE + post-exit price analysis |
| 16 | What % of MFE is captured at TP? | MFE_capture = R_achieved / MFE_R; only 1 winner | More winners |
| 17 | Are SL/TP distances appropriate for each pair? | All pairs used same structural geometry | Per-pair ATR-relative analysis |
| 18 | Would trailing stops improve results? | TM_TRAILING_STEP=0.0 (disabled) | Enable for subset, compare |

## Risk (MEDIUM PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 19 | What is expected max drawdown for 100 trades? | Cannot model without edge estimate | Edge estimate first |
| 20 | Does DYNAMIC sizing produce appropriate volumes? | Only tested FIXED at 0.01 | Switch to DYNAMIC with known balance |
| 21 | Does drawdown guard trigger at correct level? | Never triggered (too small losses) | Simulate or wait for natural trigger |

## Execution (LOW PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 22 | What is true execution cost (spread + slippage)? | Spread not captured in trade_truth | Populate spread_at_entry field |
| 23 | Does execution quality vary by time of day? | All trades off-session | Active session trades |
| 24 | Are there hidden broker costs on micro-lots? | No explicit commission data | Broker documentation check |

## Operational (LOW PRIORITY)

| # | Question | Why Unknown | Required To Answer |
|---|----------|-------------|-------------------|
| 25 | Can the bot run 7 days continuously? | Longest observed: ~6 hours | Extended run test |
| 26 | What happens during weekend rollover? | Never observed Friday→Monday transition | Wait for natural occurrence |
| 27 | Does MT5 timezone shift with DST? | Untested — assumed constant UTC+3 | Observe at next DST change (October) |
| 28 | How does the bot behave during news events? | Never observed high-volatility news | Observe or test with historical replay |
