"""
core/weight_tuner.py — Ghost Learning Agent Weight Auto-Tuner v1.0
==================================================================
Queries closed trades and ghost results for the last 48 hours, parses agent
scores, and performs grid search to find the optimal Technical, Sentiment,
and OrderFlow consensus weights for each market regime (Trending, Choppy, Neutral).
Saves optimized weights back into the system_state table.
"""
import json
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
import config
from database import get_conn, update_system_state

logger = logging.getLogger("ax.weight_tuner")

def tune_agent_weights(db_path: str = "") -> dict:
    """
    Grid searches Technical, Sentiment, and OrderFlow agent weight combinations
    maximizing historical PnL from resolved trades and ghost results.
    """
    try:
        path = db_path or getattr(config, "DB_PATH", "trading.db")
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        
        # 1. Fetch resolved candidates with metadata
        # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
        from database import open_db
        candidates = []
        with open_db(path, timeout=10) as conn:
            rows = conn.execute("""
                SELECT symbol, side, metadata, market_regime, status, linked_trade_id
                FROM signal_candidates
                WHERE created_at >= ?
                  AND (status IN ('TP_HIT', 'SL_HIT', 'WIN', 'LOSS', 'EXECUTED')
                       OR linked_trade_id IS NOT NULL)
            """, (cutoff,)).fetchall()
            
            for r in rows:
                c = dict(r)
                meta_str = c.get("metadata", "{}")
                try:
                    meta = json.loads(meta_str) if isinstance(meta_str, str) else (meta_str or {})
                except Exception:
                    meta = {}
                c["parsed_metadata"] = meta
                
                # Determine outcome (WIN = +1.5 R, LOSS = -1.0 R, or no change if unknown)
                outcome = None
                pnl = 0.0
                
                # Check actual closed trade outcome if executed
                if c.get("linked_trade_id"):
                    t_row = conn.execute("SELECT net_pnl, r_multiple FROM trades WHERE id=?", (c["linked_trade_id"],)).fetchone()
                    if t_row:
                        net_pnl = float(t_row["net_pnl"] or 0.0)
                        r_mult = float(t_row["r_multiple"] or 0.0)
                        if r_mult != 0:
                            pnl = r_mult
                        else:
                            pnl = 1.5 if net_pnl > 0 else -1.0
                        outcome = "WIN" if net_pnl > 0 else "LOSS"
                
                # Check ghost result if skipped
                if outcome is None:
                    status = str(c.get("status", "")).upper()
                    if status in ("TP_HIT", "WIN"):
                        outcome = "WIN"
                        pnl = 1.5
                    elif status in ("SL_HIT", "LOSS"):
                        outcome = "LOSS"
                        pnl = -1.0
                
                if outcome is not None:
                    c["outcome"] = outcome
                    c["pnl"] = pnl
                    candidates.append(c)

        if not candidates:
            logger.info("[WeightTuner] No resolved candidates found in the last 48 hours to tune weights.")
            return {}

        # 2. Group candidates by market regime category
        groups = {"trending": [], "choppy": [], "neutral": []}
        for c in candidates:
            reg = str(c.get("market_regime", "NEUTRAL")).upper()
            if reg in ("BULLISH", "BEARISH", "TRENDING_HIGH_VOL", "TRENDING_LOW_VOL"):
                groups["trending"].append(c)
            elif reg in ("CHOPPY", "SIDEWAYS", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"):
                groups["choppy"].append(c)
            else:
                groups["neutral"].append(c)

        # 3. Define candidate weight combinations (sum = 1.0)
        combinations = []
        for t in range(0, 101, 5):
            for f in range(0, 101 - t, 5):
                s = 100 - t - f
                combinations.append((t / 100.0, f / 100.0, s / 100.0))

        # 4. Tune each regime group
        tuned_results = {}
        for group_name, group_candidates in groups.items():
            if not group_candidates:
                logger.info(f"[WeightTuner] No candidates for regime group: {group_name}. Skipping tuning.")
                continue

            best_weights = None
            max_fitness = -9999.0
            
            # Default fallback baselines
            if group_name == "trending":
                default_weights = (0.6, 0.2, 0.2)
            elif group_name == "choppy":
                default_weights = (0.2, 0.5, 0.3)
            else:
                default_weights = (0.4, 0.4, 0.2)
                
            # Perform grid search
            for w_tech, w_flow, w_sent in combinations:
                fitness = 0.0
                trades_simulated = 0
                
                for c in group_candidates:
                    meta = c["parsed_metadata"]
                    tech_score = float(meta.get("tech_score", 50.0))
                    flow_score = float(meta.get("flow_score", 50.0))
                    sent_score = float(meta.get("sent_score", 50.0))
                    
                    adjusted_score = tech_score * w_tech + flow_score * w_flow + sent_score * w_sent
                    
                    # Assume entry threshold 55.0 for default decision ALLOW gating
                    if adjusted_score >= 55.0:
                        fitness += c["pnl"]
                        trades_simulated += 1
                
                # Check if this combination is the best so far
                if trades_simulated == 0:
                    fitness = -0.1  # small penalty for no trades
                
                # Tie-breaking: if fitness matches, choose weights closer to default
                if fitness > max_fitness or (fitness == max_fitness and best_weights is None):
                    max_fitness = fitness
                    best_weights = (w_tech, w_flow, w_sent)
                elif fitness == max_fitness and best_weights is not None:
                    # Choose weights closer to default by euclidean distance
                    dist_new = sum((w - d)**2 for w, d in zip((w_tech, w_flow, w_sent), default_weights))
                    dist_curr = sum((w - d)**2 for w, d in zip(best_weights, default_weights))
                    if dist_new < dist_curr:
                        best_weights = (w_tech, w_flow, w_sent)

            if best_weights:
                w_tech, w_flow, w_sent = best_weights
                tuned_results[group_name] = {
                    "w_tech": w_tech,
                    "w_flow": w_flow,
                    "w_sent": w_sent,
                    "fitness": round(max_fitness, 2),
                    "samples": len(group_candidates)
                }
                
                # Save optimized weights to system state
                # NEDEN: update_system_state'e yönlendirilmedi — bu fonksiyon
                # testlerde özel db_path ile çağrılıyor ve weight_* key'leri
                # dinamik config parametresi değil (Redis cfg senkronu gerekmez).
                # Bağlantı disiplini için database.open_db kullanılır (Faz 1.2).
                with open_db(path, timeout=10) as conn_write:
                    conn_write.execute("""
                        INSERT INTO system_state (key, value, updated_at)
                        VALUES (?, ?, datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                    """, (f"weight_tech_{group_name}", f"{w_tech:.2f}", f"{w_tech:.2f}"))
                    conn_write.execute("""
                        INSERT INTO system_state (key, value, updated_at)
                        VALUES (?, ?, datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                    """, (f"weight_flow_{group_name}", f"{w_flow:.2f}", f"{w_flow:.2f}"))
                    conn_write.execute("""
                        INSERT INTO system_state (key, value, updated_at)
                        VALUES (?, ?, datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                    """, (f"weight_sent_{group_name}", f"{w_sent:.2f}", f"{w_sent:.2f}"))
                    conn_write.commit()
                
                logger.info(f"[WeightTuner] Optimized weights for {group_name.upper()} (samples={len(group_candidates)}): "
                            f"Tech={w_tech:.2f}, Flow={w_flow:.2f}, Sent={w_sent:.2f} | PnL={max_fitness:+.2f}R")

        return tuned_results
    except Exception as e:
        logger.error(f"[WeightTuner] Auto-tuning error: {e}")
        return {}
