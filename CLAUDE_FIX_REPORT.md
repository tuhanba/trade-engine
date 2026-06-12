# CLAUDE FIX REPORT — Aurvex Stabilization Dev
**Date:** 2026-06-12  **Branch:** claude/aurvex-stabilization-dev-kw0xkv  **Base:** 7db9428

---

## Phase 0 — Sync & Diagnosis
- HEAD confirmed: `7db9428` (main)
- `quality_mult C: 0.25` present in `core/risk_engine.py` ✅
- Docker not available in dev environment; funnel analysis done via code inspection
- **Root causes identified:** 6 critical issues documented in AUDIT_EVIDENCE.md

---

## Phase 1 — P0 Critical Fixes

### 1.1 ML Online Gate (execution_engine.py, ghost_learning.py)
**Before:** `return None` silently blocked all trades when prob < threshold (446k biased samples)
**After:**
- Risk scaled 0.5x (`risk_pct`, `position_size`, `notional_size`, `max_loss`) instead of blocking
- `ghost_learning.py`: removed `ghost.get("reject_reason") != "ALLOW"` — now trains on ALL outcomes
- `execution_engine.close_trade`: calls `update_online_model` with real trade outcome
- Biased model renamed `sgd_online_model.pkl.biased_backup` (file didn't exist in dev)

**Files:** `execution_engine.py`, `core/ghost_learning.py`, `core/online_learning.py`

### 1.2 Double Threshold (ai_decision_engine.py, execution_service.py)
**Before:** AI uses fixed 35.0; ExecutionService re-checks with dynamic 45-70 (kills trades)
**After:**
- `effective_trade_threshold = max(35.0, trade_threshold - 10.0) if bypass_shields else trade_threshold`
- `execution_service.py`: removed `sig.final_score >= trade_thr` — AI ALLOW is final
- INFO log added: `[Threshold] effective=X trade_threshold=Y source=...`

**Files:** `core/ai_decision_engine.py`, `core/services/execution_service.py`

### 1.3 Kill Switch One-Way (scanner_service.py, global_risk_manager.py, event_types.py)
**Before:** Kill switch permanently stopped scanner; only restart recovered it
**After:**
- `KILL_SWITCH_DEACTIVATED` added to `EventType`
- `ScannerService`: `_paused` flag; loop sleeps 30s while paused; resumes on `KILL_SWITCH_DEACTIVATED`
- `GlobalRiskManager`: daily reset publishes `KILL_SWITCH_DEACTIVATED` + Telegram info
- Kill switch activation: improved Telegram critical alert
- **New test:** `tests/test_kill_switch_resume.py` — 3 passing tests

**Files:** `core/event_types.py`, `core/services/scanner_service.py`, `core/global_risk_manager.py`

### 1.4 TRADE_CLOSED Worker Thread (execution_engine.py, execution_service.py)
**Before:** `asyncio.get_event_loop()` in worker thread → RuntimeError → silent swallow → no Telegram
**After:**
- All event publishes use `event_bus.publish_sync()` (thread-safe)
- TP1, TP2, and TRADE_CLOSED events all fixed
- `execution_service.py` monitoring loop: removed duplicate TRADE_CLOSED publish (single source)

**Files:** `execution_engine.py`, `core/services/execution_service.py`

---

## Phase 2 — P1 Stabilization

### 2.1 SQLite busy_timeout
- Added `PRAGMA busy_timeout=5000` to `get_connection()`, `get_conn()`, `open_db()` in `database.py`
- WAL mode was already present

**Verification:** `PRAGMA journal_mode` → `wal` ✅

### 2.2 Ghost Stats SQL Fix
**Before:** Queried `paper_results.hit_tp` / `hit_stop_first` → always 0
**After:**
- `get_ghost_learning_stats()`: queries `ghost_results JOIN ghost_signals` for `virtual_outcome`
- `get_learning_summary()` in `ai_decision_engine.py`: same fix

**Files:** `core/ghost_learning.py`, `core/ai_decision_engine.py`

### 2.3 File Logging
- `async_scalp_engine.py`: `RotatingFileHandler(logs/bot.log, 20MB max, 3 backups)` replaces plain `FileHandler`
- `telegram.log` rotation: `RotatingFileHandler` is now the standard

**Files:** `async_scalp_engine.py`

### 2.4 Healthcheck Fast Mode
- `health_check.py`: `--fast --role engine/dashboard` — checks DB, heartbeat (<5min), port
- `docker-compose.yml`: updated healthcheck commands, `timeout: 30s`, removed `version: '3.8'`

**Files:** `health_check.py`, `docker-compose.yml`

### 2.5 param_audit Table
- New table: `param_audit(id, ts, key, old_value, new_value, actor, reason)`
- `set_state(key, value, actor="system", reason="")` — writes audit for tracked keys
- Tracked: `trade_threshold`, `risk_pct`, `regime_filter_min_quality_in_choppy`, `confirmation_mode`

**Files:** `database.py`

---

## Phase 3 — Development & Cleanup

### 3.1 Backtest Temp Cleanup
- `aurvex_maintain.sh`: `find ... -name "backtest_temp_*.db" -mtime +1 -delete`

### 3.2 .gitignore Update
- Added: `backtest_data/`, `backtest_temp_*.db`, `logs/`, `core/backups/`, `*.db-wal`, `*.db-shm`

### 3.3 docker-compose Hardening
- `env_file: .env` added to engine and dashboard services
- Removed deprecated `version: '3.8'`
- Updated healthcheck commands and timeouts

### 3.4 Funnel Observability
- `scripts/daily_health_report.py`: signal funnel table (SCANNED→EXECUTED counts), top 5 reject_reasons
- Sends formatted Telegram message at 21:00 UTC (hook in async_scalp_engine needed for scheduling)

### 3.5 Threshold Writers Deduplication
- `config.py __getattr__`: removed regime modifier for `TRADE_THRESHOLD` (+5/+3/-2)
- Friday already manages threshold; double-application removed

---

## Phase 4 — Friday Cost & Security

### 4.1 Default Provider: Offline
- `config.py`: `FRIDAY_LLM_MODE=offline` (default), `FRIDAY_LLM_DAILY_BUDGET=5`
- `friday_ceo.py`: respects `FRIDAY_LLM_MODE`; `offline` skips all LLM calls

### 4.2 Call Budget
- `friday_ceo.py`: tracks `friday_llm_calls_today` in `system_state`
- At budget: switches to offline, sends one Telegram notification per day

### 4.3 Parameter Clamps
- `friday_ceo.py._apply_param_with_clamp()`: clamps values before writing
  - `trade_threshold`: [40, 70]
  - `risk_pct`: [0.25, 1.5]
  - Out-of-range → clamped, `reason="clamped"` written to `param_audit`

### 4.4 Friday Audit + /friday_log
- `telegram_manager.py`: `/friday_log` command shows last 10 `param_audit` records with `actor='friday'`

---

## Final Validation Checklist
| Check | Status |
|-------|--------|
| `pytest tests/ -q` (44 tests, deps excluded) | ✅ PASS |
| `KILL_SWITCH_DEACTIVATED` in EventType | ✅ |
| `get_event_loop` removed from execution_engine.py | ✅ |
| Ghost stats uses ghost_results table | ✅ |
| param_audit table in database.py | ✅ |
| FRIDAY_LLM_MODE=offline default | ✅ |
| ML gate scales instead of blocks | ✅ |
| Double threshold removed from execution_service | ✅ |
| Docker healthcheck timeout 30s, --fast mode | ✅ |

---

## Deferred / Not Done in This Run
1. **Git history cleanup** (`git filter-repo` to remove `backtest_data/` from history) — requires separate decision
2. **Dashboard Redis migration** — separate architectural task
3. **ML gate re-training threshold:** min 100 real closed trades before re-enabling full blocking mode
4. **Prometheus metrics** (`ax_signals_rejected_total`, `ax_trades_opened_total`) — `core/metrics.py` enhancement
5. **Daily 21:00 UTC health report trigger** — needs scheduler integration in `async_scalp_engine.py`
6. **Docker container restart verification** — not available in dev environment
