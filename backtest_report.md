# AX Trade Engine Backtest Performance Report

This report presents the backtest metrics of the current trading bot engine running in paper trading simulation mode.

## 📊 Summary Performance Metrics

| Metric | Value |
| :--- | :--- |
| **Backtest Period** | 2026-05-31 to 2026-06-03 |
| **Analyzed Symbols** | `BTCUSDT` |
| **Total Trades Executed** | 9 |
| **Win Rate** | **0.0%** (0 Wins / 9 Losses) |
| **Initial Capital** | $2000.00 |
| **Final Capital** | $1981.20 |
| **Net Profit / Return** | **$-18.80** (-0.94%) |
| **Profit Factor** | **0.00** |
| **Max Portfolio Drawdown** | **0.9%** |
| **Average Win** | $0.00 |
| **Average Loss** | $2.09 |

---

## 🎯 Rejection Funnel Analysis

| Funnel Step | Passed | Filtered / Failed | Detail / Reason |
| :--- | :---: | :---: | :--- |
| **Total Candidates Scanned** | 841 | - | Scanned universe |
| **Trend Filter** | 669 | 172 | Direction == NO TRADE |
| **Trigger Filter** | 66 | 603 | Setup quality == D / Invalid params |
| **Risk Filter** | 10 | 56 | Invalid stop-loss, take-profit or leverage |
| **AI Decision Filter** | 10 | 0 | VETOED (0) / WATCHED (0) |
| **Execution Gate** | 9 | 1 | Score below threshold (0) or invalid quality (1) |

---

## 🔍 Exit Reasons Breakdown

| Exit Reason | Count | Percentage |
| :--- | :---: | :---: |
| STOP_LOSS | 9 | 100.0% |

---

## 📈 Performance by Asset

| Asset | Trades | Wins | Losses | Win Rate | Net PnL ($) | Return (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **BTCUSDT** | 9 | 0 | 9 | 0.0% | -18.80$ | -0.94% |

---

## 📝 Trade Logs Detail

| ID | Symbol | Direction | Entry | Close | Realized PnL | Exit Reason | Timestamp |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
| #1 | BTCUSDT | SHORT | 48985.5661 | 49161.8918 | -2.1101$ | STOP_LOSS | 2026-05-31T21:06:00+00:00 |
| #2 | BTCUSDT | SHORT | 48015.6608 | 48306.3816 | -3.2059$ | STOP_LOSS | 2026-05-31T23:16:00+00:00 |
| #3 | BTCUSDT | LONG | 57367.2650 | 57214.9634 | -6.4190$ | STOP_LOSS | 2026-06-01T06:26:00+00:00 |
| #4 | BTCUSDT | SHORT | 44312.7292 | 44357.2128 | -0.5650$ | STOP_LOSS | 2026-06-01T13:46:00+00:00 |
| #5 | BTCUSDT | LONG | 49177.0207 | 49173.1040 | -0.2847$ | STOP_LOSS | 2026-06-01T20:06:00+00:00 |
| #6 | BTCUSDT | SHORT | 39728.7934 | 39742.6831 | -0.3617$ | STOP_LOSS | 2026-06-02T06:11:00+00:00 |
| #7 | BTCUSDT | LONG | 49627.6626 | 49437.5932 | -1.4487$ | STOP_LOSS | 2026-06-02T14:06:00+00:00 |
| #8 | BTCUSDT | LONG | 60807.7062 | 60355.7485 | -2.6362$ | STOP_LOSS | 2026-06-03T06:01:00+00:00 |
| #9 | BTCUSDT | SHORT | 52564.6799 | 52838.3613 | -1.7687$ | STOP_LOSS | 2026-06-03T13:36:00+00:00 |
