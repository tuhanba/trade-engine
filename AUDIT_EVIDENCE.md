# AUDIT EVIDENCE — Aurvex Stabilization Dev Run
**Date:** 2026-06-12  **Branch:** claude/aurvex-stabilization-dev-kw0xkv  **Base:** 7db9428

## Phase 0 — Git Sync
- HEAD: 7db9428 (feat(llm): add multi-provider LLM support)
- Branch: claude/aurvex-stabilization-dev-kw0xkv
- Status: Clean, no stash needed
- `grep -n "quality_mult" core/risk_engine.py` → present (C: 0.25 multiplier)

## Funnel Diagnosis (Docker not available in dev environment)
- Signal flow: SCANNED → TREND → TRIGGER → RISK → AI_VALIDATED → EXECUTION
- Known issues identified by code analysis:
  1. ML Online Gate: returns None on low prob (blocks all trades silently)
  2. Double threshold: AI uses 35.0 fixed, ExecutionService uses dynamic 45-70
  3. Kill switch: scanner stops permanently, no resume path
  4. TRADE_CLOSED: asyncio.get_event_loop() in worker thread fails silently
  5. Ghost stats: queries paper_results hit_tp/hit_stop_first cols (always 0)
  6. Biased ML model: only trained on rejected signals (446k samples, skip count)

## Changes Applied
See CLAUDE_FIX_REPORT.md for full report.
