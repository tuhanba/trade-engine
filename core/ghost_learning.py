"""
core/ghost_learning.py — Ghost Learning 2.0
============================================
WATCH, VETO ve SKIPPED sinyalleri hem paper_results (v1 uyumu için) hem de
ayrı ghost_signals tablosuna kaydedilir. Ghost Learning 2.0 şunları ekler:

  - maybe_ghost_log()          : reddedilen sinyali ghost_signals'e yaz
  - simulate_pending_ghosts()  : mevcut fiyatla hızlı simülasyon
  - get_pattern_analysis()     : trigger_type × coin bazında WR / R analizi
  - generate_threshold_suggestions() : AI Brain'e öneri üret
  - summarize_ghost_results()  : Ajan-1 / dashboard özeti
  - send_weekly_ghost_report() : Telegram haftalık rapor

v1 fonksiyonları korunmuştur (track_skipped_signal, process_pending_results,
calculate_dynamic_ghost_weight, get_ghost_learning_stats).

Ağırlıklar:
  Gerçek trade  = 1.0
  Ghost trade   = 0.25–0.35
  Son 30 gün    = daha yüksek ağırlık
"""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

try:
    from config import GHOST_WEIGHT as _GHOST_WEIGHT_FLOOR
except Exception:
    _GHOST_WEIGHT_FLOOR = 0.15

GHOST_WEIGHT    = _GHOST_WEIGHT_FLOOR
HORIZON_MINUTES = 480
EXPIRY_HOURS    = 12

_TRACKED_DECISIONS = {
    "WATCH", "VETO", "SKIPPED_BY_RISK", "SKIPPED_BY_REGIME",
    "SKIPPED_BY_LOW_SCORE", "SKIPPED_BY_COOLDOWN",
    "SKIPPED_BY_SPREAD", "SKIPPED_BY_MARGIN_RISK",
}


# ── Ghost Learning 2.0 ────────────────────────────────────────────────

def maybe_ghost_log(signal: dict, reason: str) -> None:
    """
    Reddedilen sinyali ghost_signals tablosuna ekler.
    Confidence > 0.40 olan sinyaller için çalışır (tamamen berbatları alma).
    signal: dict with keys: symbol/coin, side/direction, entry, sl, tp1,
            confidence, trigger_type (opsiyonel)
    """
    try:
        confidence = float(signal.get("confidence") or signal.get("final_score", 0) / 100)
        
        from database import save_ghost_signal
        # P1 BUG FIX #5: symbol alanı her zaman boş kalıyordu.
        # maybe_ghost_log() "coin" key ile sinyal gönderiyordu,
        # save_ghost_signal() ise "symbol" key'ini okuyordu → boş string.
        # Artık her ikisini de deniyor.
        _sym = signal.get("symbol") or signal.get("coin") or ""
        ghost_id = save_ghost_signal({
            "coin":         _sym,
            "symbol":       _sym,   # P1 FIX: symbol alanı da dolduruldu
            "side":         signal.get("direction") or signal.get("side", ""),
            "entry_price":  signal.get("entry") or signal.get("entry_zone") or signal.get("entry_price", 0),
            "stop_loss":    signal.get("sl") or signal.get("stop_loss", 0),
            "take_profit":  signal.get("tp1") or signal.get("take_profit", 0),
            "tp1":          signal.get("tp1", 0),
            "tp2":          signal.get("tp2", 0),
            "confidence":   confidence,
            "reject_reason": reason,
            "trigger_type": signal.get("trigger_type") or signal.get("setup_quality") or signal.get("quality", "unknown"),
            "final_score":  signal.get("final_score", 0),
            "market_regime": signal.get("market_regime", "NEUTRAL"),
        })
        logger.debug(
            "[Ghost2] %s ghost#%d kaydedildi reason=%s",
            _sym, ghost_id, reason
        )
    except Exception as e:
        logger.warning("[Ghost2] maybe_ghost_log hatası: %s", e)



def simulate_pending_ghosts(client, limit: int = 100, prices: dict = None) -> int:
    """
    Simüle edilmemiş ghost_signals kayıtlarını mevcut fiyatla işler.
    Returns: simüle edilen kayıt sayısı.
    """
    try:
        from database import get_unsimulated_ghosts, save_ghost_result
        ghosts = get_unsimulated_ghosts(limit=limit)
        if not ghosts:
            return 0

        if prices is None:
            prices = _get_all_prices(client)

        count = 0
        now = datetime.now(timezone.utc)
        for ghost in ghosts:
            try:
                _simulate_single_ghost(ghost, client, now, prices)
                count += 1
            except Exception as e:
                logger.warning("[Ghost2] ghost#%s simülasyon hatası: %s", ghost.get("id"), e)
        return count
    except Exception as e:
        logger.error("[Ghost2] simulate_pending_ghosts hatası: %s", e)
        return 0


def _simulate_single_ghost(ghost: dict, client, now: datetime, prices: dict = None) -> None:
    """Tek ghost kaydını mevcut fiyatla simüle eder."""
    from database import save_ghost_result

    ghost_id   = ghost["id"]
    symbol     = ghost["coin"]
    side       = str(ghost.get("side", "")).upper()
    entry      = float(ghost["entry_price"])
    sl         = float(ghost["stop_loss"])
    tp         = float(ghost["take_profit"])
    quality    = ghost.get("trigger_type", "unknown")

    # Zaman aşımı: 12 saatten eski → OPEN olarak kapat
    try:
        created = datetime.fromisoformat(str(ghost["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - created).total_seconds() / 3600
    except Exception:
        elapsed_hours = 0

    if elapsed_hours > EXPIRY_HOURS:
        save_ghost_result(ghost_id, {
            "virtual_outcome": "OPEN",
            "virtual_pnl_r":   0,
            "pattern_type":    quality,
        })
        return

    if prices is not None:
        price = prices.get(symbol)
    else:
        price = _get_price(client, symbol)
        
    if not price:
        return

    is_long  = side == "LONG"
    tp_hit   = (is_long and price >= tp) or (not is_long and price <= tp)
    sl_hit   = (is_long and price <= sl)  or (not is_long and price >= sl)

    risk = abs(entry - sl)
    mfe  = 0.0
    mae  = 0.0
    if risk > 0:
        if is_long:
            mfe = max(0.0, (price - entry) / risk)
            mae = max(0.0, (entry - price) / risk)
        else:
            mfe = max(0.0, (entry - price) / risk)
            mae = max(0.0, (price - entry) / risk)

    if tp_hit:
        pnl_r = round((abs(tp - entry) / risk), 2) if risk > 0 else 1.0
        outcome = "WIN"
    elif sl_hit:
        pnl_r   = -1.0
        outcome = "LOSS"
    else:
        pnl_r   = 0.0
        outcome = "OPEN"

    save_ghost_result(ghost_id, {
        "virtual_outcome": outcome,
        "virtual_pnl_r":   pnl_r,
        "virtual_mfe":     round(mfe, 4),
        "virtual_mae":     round(mae, 4),
        "pattern_type":    quality,
    })
    logger.debug(
        "[Ghost2] ghost#%d %s %s → %s pnl_r=%.2f",
        ghost_id, symbol, side, outcome, pnl_r
    )


def get_pattern_analysis(min_count: int = 5, days: int = 30) -> list:
    """
    Hangi trigger_type'lar ghost WIN getiriyor?
    Dönen liste: [{trigger_type, coin, ghost_count, virtual_wr, avg_virtual_r}, ...]
    Sadece anlamlı pattern'lar (min_count >= 5, wr > 0).
    """
    try:
        from database import get_ghost_pattern_stats
        return get_ghost_pattern_stats(min_count=min_count, days=days)
    except Exception as e:
        logger.error("[Ghost2] get_pattern_analysis hatası: %s", e)
        return []


def generate_threshold_suggestions(ghost_stats: list | None = None) -> list:
    """
    Ghost pattern analizine göre threshold düşürme önerileri üretir.
    Önerileri ghost_suggestions ve ghost_threshold_suggestions tablolarına kaydeder.
    Returns: önerilerin listesi.
    """
    if ghost_stats is None:
        ghost_stats = get_pattern_analysis()

    suggestions = []
    try:
        from database import save_ghost_suggestion

        try:
            import config
            _current_threshold = getattr(config, "TRADE_THRESHOLD", 72.0)
        except Exception:
            _current_threshold = 72.0

        for stat in ghost_stats:
            wr  = float(stat.get("virtual_wr", 0))
            avg = float(stat.get("avg_virtual_r", 0))
            cnt = int(stat.get("ghost_count", 0))

            if wr > 60 and avg > 0.8:
                suggested = max(_current_threshold - 5.0, 45.0)
                confidence = "HIGH" if cnt > 30 else "MEDIUM"
                sug = {
                    "coin":           stat.get("coin", "ALL"),
                    "symbol":         stat.get("coin", "ALL"),
                    "trigger_type":   stat.get("trigger_type", "unknown"),
                    "action":         "LOWER_THRESHOLD",
                    "current_val":    _current_threshold,
                    "suggested_val":  suggested,
                    "expected_trades": round(cnt / 4, 1),
                    "confidence":     confidence,
                    "virtual_wr":     wr,
                    "avg_virtual_r":  avg,
                    "sample_count":   cnt,
                }
                save_ghost_suggestion(sug)
                suggestions.append(sug)
            elif (wr < 40 or avg < 0.0) and cnt >= 5:
                # Eşik Yükseltme (Cooling Down)
                suggested = min(_current_threshold + 5.0, 85.0)
                confidence = "HIGH" if cnt > 15 else "MEDIUM"
                sug = {
                    "coin":           stat.get("coin", "ALL"),
                    "symbol":         stat.get("coin", "ALL"),
                    "trigger_type":   stat.get("trigger_type", "unknown"),
                    "action":         "RAISE_THRESHOLD",
                    "current_val":    _current_threshold,
                    "suggested_val":  suggested,
                    "expected_trades": round(cnt / 4, 1),
                    "confidence":     confidence,
                    "virtual_wr":     wr,
                    "avg_virtual_r":  avg,
                    "sample_count":   cnt,
                }
                save_ghost_suggestion(sug)
                suggestions.append(sug)
    except Exception as e:
        logger.error("[Ghost2] generate_threshold_suggestions hatası: %s", e)

    return suggestions


def summarize_ghost_results() -> dict:
    """
    Ajan-1 / dashboard için ghost learning özeti.
    Hem ghost_signals/ghost_results (2.0) hem paper_results (v1) kapsanır.
    Returns dict: {total, wins, losses, win_rate, avg_r, pending,
                   top_patterns, ghost_pnl, correct_skips}
    """
    try:
        from database import get_conn

        with get_conn() as conn:
            # ── Ghost 2.0 istatistikleri (ghost_results) ──────────────
            g2 = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN r.virtual_outcome='WIN'  THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN r.virtual_outcome='LOSS' THEN 1 ELSE 0 END) as losses,
                    AVG(r.virtual_pnl_r) as avg_r,
                    SUM(r.virtual_pnl_r) as ghost_pnl
                FROM ghost_signals g
                JOIN ghost_results r ON g.id = r.ghost_id
                WHERE r.virtual_outcome IN ('WIN','LOSS')
            """).fetchone()

            pending_g2 = conn.execute(
                "SELECT COUNT(*) FROM ghost_signals WHERE simulated=0"
            ).fetchone()[0]

            # ── Top patterns (2.0) ────────────────────────────────────
            top_rows = conn.execute("""
                SELECT
                    r.pattern_type,
                    COUNT(*) as cnt,
                    SUM(CASE WHEN r.virtual_outcome='WIN' THEN 1.0 ELSE 0 END)
                        * 100.0 / COUNT(*) as wr,
                    AVG(r.virtual_pnl_r) as avg_r
                FROM ghost_results r
                WHERE r.virtual_outcome IN ('WIN','LOSS')
                  AND r.simulated_at > datetime('now','-30 days')
                GROUP BY r.pattern_type
                HAVING cnt >= 3
                ORDER BY avg_r DESC
                LIMIT 5
            """).fetchall()

            # ── v1 paper_results özeti ────────────────────────────────
            p1 = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN would_have_won=1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN hit_stop_first=1 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN skip_decision_correct=1 THEN 1 ELSE 0 END) as correct_skips
                FROM paper_results
                WHERE status IN ('finalized', 'completed')
            """).fetchone()

        g2_total  = int(g2["total"] or 0)
        g2_wins   = int(g2["wins"]  or 0)
        g2_losses = int(g2["losses"] or 0)
        g2_wr     = round(g2_wins / g2_total * 100, 1) if g2_total > 0 else 0.0
        g2_avg_r  = round(float(g2["avg_r"] or 0), 3)
        g2_pnl    = round(float(g2["ghost_pnl"] or 0), 3)

        top_patterns = [
            {
                "pattern":  dict(r)["pattern_type"],
                "count":    int(dict(r)["cnt"]),
                "win_rate": round(float(dict(r)["wr"]), 1),
                "avg_r":    round(float(dict(r)["avg_r"]), 2),
            }
            for r in top_rows
        ]

        p1_total = int(p1["total"] or 0) if p1 else 0

        return {
            "total":          g2_total,
            "wins":           g2_wins,
            "losses":         g2_losses,
            "win_rate":       g2_wr,
            "avg_r":          g2_avg_r,
            "ghost_pnl":      g2_pnl,
            "pending":        pending_g2,
            "top_patterns":   top_patterns,
            "paper_v1_total": p1_total,
            "correct_skips":  int(p1["correct_skips"] or 0) if p1 else 0,
        }
    except Exception as e:
        logger.error("[Ghost2] summarize_ghost_results hatası: %s", e)
        return {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "avg_r": 0, "ghost_pnl": 0, "pending": 0,
            "top_patterns": [], "paper_v1_total": 0, "correct_skips": 0,
        }


def send_weekly_ghost_report(bot_token: str = "", chat_id: str = "") -> bool:
    """
    Haftalık ghost learning özeti Telegram'a gönderir.
    bot_token/chat_id boş ise config'den alır.
    Returns True if sent successfully.
    """
    try:
        if not bot_token or not chat_id:
            from config import TELEGRAM_BOT_TOKEN as bot_token, TELEGRAM_CHAT_ID as chat_id

        summary = summarize_ghost_results()
        suggestions = generate_threshold_suggestions()

        week_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
        week_end   = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        lines = [
            "👻 Ghost Learning Haftalık Rapor",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 Hafta: {week_start} → {week_end}",
            f"🔍 Simüle edilen: {summary['total']} ghost signal",
            f"✅ WIN: {summary['wins']}  ❌ LOSS: {summary['losses']}  WR: {summary['win_rate']}%",
            f"💰 Ghost PnL: {summary['ghost_pnl']:+.2f}R  Ort. R: {summary['avg_r']:+.2f}R",
            f"⏳ Bekleyen: {summary['pending']}",
            "",
        ]

        if summary["top_patterns"]:
            lines.append("🏆 En İyi Missed Pattern'lar")
            for p in summary["top_patterns"]:
                lines.append(
                    f"  {p['pattern']:12s}  WR:{p['win_rate']:.0f}%  R:{p['avg_r']:+.2f}  ({p['count']} ghost)"
                )
            lines.append("")

        if suggestions:
            lines.append("💡 Threshold Önerileri")
            for s in suggestions[:5]:
                arrow = "⬇️" if s["action"] == "LOWER_THRESHOLD" else "⬆️"
                lines.append(
                    f"  {s['coin']:12s} {s['trigger_type']:8s}: "
                    f"{s['current_val']:.0f} → {s['suggested_val']:.0f} {arrow}"
                )
            lines.append("")

        if summary["correct_skips"]:
            lines.append(f"🎯 Doğru Atlama Kararı: {summary['correct_skips']} sinyal")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        text = "\n".join(lines)

        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": ""},
            timeout=10,
        )
        ok = resp.status_code == 200
        if ok:
            logger.info("[Ghost2] Haftalık rapor Telegram'a gönderildi")
        else:
            logger.warning("[Ghost2] Telegram gönderim hatası: %s", resp.text[:200])
        return ok
    except Exception as e:
        logger.error("[Ghost2] send_weekly_ghost_report hatası: %s", e)
        return False


# ── v1 Uyumluluk — mevcut paper_results pipeline'ı korunur ───────────

def track_skipped_signal(signal: dict, decision: str, client=None):
    """
    WATCH, VETO veya SKIPPED sinyal için paper_results'a takip kaydı açar.
    Ghost Learning 2.0 ile maybe_ghost_log() da çağrılır.
    """
    try:
        from database import save_paper_trade

        if decision not in _TRACKED_DECISIONS:
            return

        entry = signal.get("entry") or signal.get("entry_zone")
        sl    = signal.get("sl") or signal.get("stop_loss")
        tp1   = signal.get("tp1")

        if not entry or not sl or not tp1:
            return

        save_paper_trade({
            "symbol":    signal.get("symbol"),
            "direction": signal.get("direction"),
            "entry":     entry,
            "sl":        sl,
            "tp1":       tp1,
        }, tracked_from=decision)

        # Ghost 2.0: ayrı tabloya da kaydet
        maybe_ghost_log(signal, reason=decision)

        logger.debug(
            "[Ghost] %s %s takibe alındı entry=%s sl=%s tp1=%s",
            signal.get("symbol"), decision, entry, sl, tp1
        )
    except Exception as e:
        logger.warning("[Ghost] track_skipped_signal hatası: %s", e)


def process_pending_results(client, prices=None):
    """
    Bekleyen paper_results kayıtlarını mevcut fiyatla işle (v1 pipeline).
    Ghost 2.0 simülasyonu da çalıştırılır.
    Returns: işlenen toplam kayıt sayısı (int)
    """
    processed_count = 0
    try:
        from database import get_pending_paper_results

        pending = get_pending_paper_results(limit=50)
        if prices is None:
            prices = _get_all_prices(client)
        
        if pending:
            now = datetime.now(timezone.utc)
            for record in pending:
                try:
                    _process_single(record, client, now, prices)
                    processed_count += 1
                except Exception as e:
                    logger.warning("[Ghost] Kayıt #%s işlenemedi: %s", record.get("id"), e)

        # Ghost 2.0 simülasyonunu da çalıştır
        done = simulate_pending_ghosts(client, limit=100, prices=prices)
        if done:
            logger.debug("[Ghost2] %d ghost simüle edildi", done)
        processed_count += done

    except Exception as e:
        logger.error("[Ghost] process_pending_results hatası: %s", e)

    return processed_count  # P2 BUG FIX #13: None yerine int döndür



def _calc_excursions(entry: float, sl: float, current_price: float, is_long: bool) -> tuple[float, float]:
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0, 0.0
    if is_long:
        mfe = max(0.0, (current_price - entry) / risk)
        mae = max(0.0, (entry - current_price) / risk)
    else:
        mfe = max(0.0, (entry - current_price) / risk)
        mae = max(0.0, (current_price - entry) / risk)
    return round(mfe, 4), round(mae, 4)


def _calc_skip_correctness(hit_tp: int, hit_stop_first: int, tracked_from: str) -> int:
    if tracked_from in ("VETO", "SKIPPED_BY_RISK", "SKIPPED_BY_REGIME",
                        "SKIPPED_BY_LOW_SCORE", "SKIPPED_BY_COOLDOWN",
                        "SKIPPED_BY_SPREAD", "SKIPPED_BY_MARGIN_RISK"):
        if hit_stop_first == 1 and hit_tp == 0:
            return 1
        elif hit_tp == 1 and hit_stop_first == 0:
            return 0
    return -1


def _process_single(record: dict, client, now: datetime, prices: dict = None):
    """Tek bir paper_result kaydını işle (v1)."""
    from database import update_paper_result

    record_id = record["id"]
    symbol    = record["symbol"]
    direction = record["direction"]
    entry     = float(record.get("preview_entry") or 0)
    sl        = float(record.get("preview_sl") or 0)
    tp1       = float(record.get("preview_tp1") or 0)

    if not entry or not sl or not tp1:
        update_paper_result(record_id, {
            "status":       "finalized",
            "finalized_at": now.isoformat(),
            "skip_decision_correct": None,
        })
        return

    try:
        created = datetime.fromisoformat(str(record["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        elapsed_hours = (now - created).total_seconds() / 3600
    except Exception:
        elapsed_hours = 0

    if elapsed_hours > EXPIRY_HOURS:
        update_paper_result(record_id, {
            "status":         "finalized",
            "finalized_at":   now.isoformat(),
            "hit_tp":         0,
            "hit_stop_first": 0,
            "setup_worked":   0,
        })
        logger.debug("[Ghost] #%d %s EXPIRED (%.1fsa)", record_id, symbol, elapsed_hours)
        return

    if prices is not None:
        current_price = prices.get(symbol)
    else:
        current_price = _get_price(client, symbol)
        
    if not current_price:
        return

    is_long = str(direction).upper() == "LONG"
    tp1_hit = (is_long and current_price >= tp1) or (not is_long and current_price <= tp1)
    sl_hit  = (is_long and current_price <= sl)  or (not is_long and current_price >= sl)

    cur_mfe, cur_mae = _calc_excursions(entry, sl, current_price, is_long)
    prev_mfe = float(record.get("max_favorable_excursion") or 0)
    prev_mae = float(record.get("max_adverse_excursion") or 0)
    new_mfe  = max(prev_mfe, cur_mfe)
    new_mae  = max(prev_mae, cur_mae)

    tracked_from = str(record.get("tracked_from") or "")

    if tp1_hit:
        correctness = _calc_skip_correctness(1, 0, tracked_from)
        updates = {
            "status":                  "finalized",
            "finalized_at":            now.isoformat(),
            "hit_tp":                  1,
            "hit_stop_first":          0,
            "setup_worked":            1,
            "would_have_won":          1,
            "max_favorable_excursion": new_mfe,
            "max_adverse_excursion":   new_mae,
            "first_touch":             "tp1",
        }
        if correctness >= 0:
            updates["skip_decision_correct"] = correctness
        update_paper_result(record_id, updates)
        logger.info("[Ghost] #%d %s GHOST_WIN MFE=%.2fR MAE=%.2fR", record_id, symbol, new_mfe, new_mae)

    elif sl_hit:
        correctness = _calc_skip_correctness(0, 1, tracked_from)
        updates = {
            "status":                  "finalized",
            "finalized_at":            now.isoformat(),
            "hit_tp":                  0,
            "hit_stop_first":          1,
            "setup_worked":            0,
            "would_have_won":          0,
            "max_favorable_excursion": new_mfe,
            "max_adverse_excursion":   new_mae,
            "first_touch":             "stop",
        }
        if correctness >= 0:
            updates["skip_decision_correct"] = correctness
        update_paper_result(record_id, updates)
        logger.info("[Ghost] #%d %s GHOST_LOSS MFE=%.2fR MAE=%.2fR", record_id, symbol, new_mfe, new_mae)

    else:
        if new_mfe > prev_mfe or new_mae > prev_mae:
            update_paper_result(record_id, {
                "max_favorable_excursion": new_mfe,
                "max_adverse_excursion":   new_mae,
            })


def _get_price(client, symbol: str) -> float:
    try:
        if client:
            ticker = client.futures_ticker(symbol=symbol)
            return float(ticker["lastPrice"])
    except Exception:
        pass
    # BUG FIX: Public API fallback — client yoksa veya başarısız olursa
    try:
        import requests as _req
        r = _req.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        if r.status_code == 200:
            return float(r.json()["price"])
    except Exception as _e:
        logger.debug("[Ghost] Public API fallback hatası: %s", _e)
    return 0.0

def _get_all_prices(client) -> dict:
    """Fetch all symbol prices at once to avoid blocking network calls."""
    try:
        from core.market_data import _PRICE_CACHE
        if _PRICE_CACHE:
            return dict(_PRICE_CACHE)
    except Exception:
        pass

    prices = {}
    try:
        if client:
            tickers = client.futures_ticker()
            if isinstance(tickers, list):
                for t in tickers:
                    prices[t["symbol"]] = float(t["lastPrice"])
                return prices
    except Exception:
        pass
    try:
        import requests as _req
        r = _req.get("https://fapi.binance.com/fapi/v1/ticker/price", timeout=5)
        if r.status_code == 200:
            for t in r.json():
                prices[t["symbol"]] = float(t["price"])
    except Exception as _e:
        logger.debug("[Ghost] _get_all_prices fallback hatası: %s", _e)
    return prices


def calculate_dynamic_ghost_weight() -> float:
    """
    Gerçek trade sayısına göre ghost ağırlığını dinamik hesapla.
    Az trade → yüksek ağırlık (daha fazla öğren).
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE LOWER(status)='closed'"
            ).fetchone()
        real_trades = row[0] if row else 0

        if real_trades < 10:
            return 0.45
        elif real_trades < 30:
            return 0.35
        elif real_trades < 100:
            return 0.25
        elif real_trades < 300:
            return 0.15
        else:
            return max(_GHOST_WEIGHT_FLOOR, 0.10)
    except Exception:
        return _GHOST_WEIGHT_FLOOR


def get_ghost_learning_stats() -> dict:
    """
    Ghost learning istatistiklerini döndür (v1 uyumluluk).
    AI karar motorunun coin profil hesabında kullanılır.
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN hit_tp=1 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN hit_stop_first=1 THEN 1 ELSE 0 END) as losses,
                    SUM(CASE WHEN skip_decision_correct=1 THEN 1 ELSE 0 END) as correct_skips,
                    AVG(max_favorable_excursion) as avg_mfe,
                    AVG(max_adverse_excursion) as avg_mae
                FROM paper_results
                WHERE status IN ('finalized', 'completed')
            """).fetchone()

        if not row:
            return {}

        total = row[0] or 0
        return {
            "total":          total,
            "ghost_wins":     row[1] or 0,
            "ghost_losses":   row[2] or 0,
            "ghost_win_rate": round((row[1] or 0) / total, 4) if total > 0 else 0,
            "correct_skips":  row[3] or 0,
            "avg_mfe":        round(float(row[4] or 0), 4),
            "avg_mae":        round(float(row[5] or 0), 4),
            "weight":         GHOST_WEIGHT,
            "dynamic_weight": calculate_dynamic_ghost_weight(),
        }
    except Exception as e:
        logger.error("[Ghost] get_ghost_learning_stats hatası: %s", e)
        return {}


def apply_ghost_suggestions_v2(min_confidence: str = "MEDIUM") -> list:
    """
    ghost_suggestions tablosundaki uygulanmamış önerileri okur,
    ilgili coin'in coin_configs tablosundaki JSON parametrelerine 'threshold_overrides'
    olarak ekler ve öneriyi 'applied' olarak işaretler.
    Ayrıca Telegram üzerinden bildirim gönderir.

    Returns: uygulanan değişikliklerin string listesi.
    """
    from database import get_pending_ghost_suggestions, get_coin_config, save_coin_config, mark_ghost_suggestion_applied
    from telegram_delivery import send_message

    applied_changes = []
    try:
        suggestions = get_pending_ghost_suggestions(min_confidence=min_confidence)
        if not suggestions:
            logger.info("[Ghost2] Uygulanacak yeni otonom öneri yok.")
            return []

        # Aynı coin + trigger_type için en yüksek avg_virtual_r getiren öneriyi seç
        best_per_combo = {}
        for s in suggestions:
            sym = s["symbol"]
            trigger = s["trigger_type"]
            combo_key = (sym, trigger)
            if combo_key not in best_per_combo or s["avg_virtual_r"] > best_per_combo[combo_key]["avg_virtual_r"]:
                best_per_combo[combo_key] = s

        for (sym, trigger), s in best_per_combo.items():
            current_cfg = get_coin_config(sym)
            
            # config_json içinde overrides dictionary'sini al veya oluştur
            overrides = current_cfg.setdefault("threshold_overrides", {})
            
            import config
            global_thr = config.HUMAN_TRADE_THRESHOLD if getattr(config, "HUMAN_MODE", False) else getattr(config, "TRADE_THRESHOLD", 72.0)
            old_thr = overrides.get(trigger, s.get("current_threshold") or global_thr)
            new_thr = s["suggested_threshold"]

            # Eylem yönünü belirle
            is_raise = new_thr > old_thr

            # Güvenlik: Tek seferde maksimum 5 puan değiştir
            if abs(old_thr - new_thr) > 5.0:
                change = 5.0 if is_raise else -5.0
                new_thr = round(old_thr + change, 1)

            # Eylem yönüne göre kontrol
            if is_raise:
                if new_thr <= old_thr:
                    mark_ghost_suggestion_applied(s["id"])
                    continue
            else:
                if new_thr >= old_thr:
                    mark_ghost_suggestion_applied(s["id"])
                    continue

            overrides[trigger] = new_thr
            current_cfg["ghost_applied_at"]  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            current_cfg["ghost_virtual_wr"]  = s["virtual_wr"]
            current_cfg["ghost_avg_r"]       = s["avg_virtual_r"]
            current_cfg["ghost_sample"]      = s["sample_count"]

            save_coin_config(sym, current_cfg)
            mark_ghost_suggestion_applied(s["id"])

            msg = (
                f"👻 {sym} ({trigger}): eşik {old_thr:.1f} → {new_thr:.1f} "
                f"[Sanal WR: {s['virtual_wr']:.1f}% | Ort. R: {s['avg_virtual_r']:.2f} | n: {s['sample_count']}]"
            )
            applied_changes.append(msg)
            logger.info("[GhostApply] %s", msg)

        if applied_changes:
            report = (
                "🤖 <b>Otonom Ghost Learning Optimizasyonu</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Aşağıdaki coin/pattern parametre eşikleri arka plan simülasyon başarısına göre otonom olarak düşürüldü:\n\n"
                + "\n".join(applied_changes)
            )
            try:
                send_message(report)
            except Exception as e:
                logger.warning(f"Otonom Telegram bildirimi gönderilemedi: {e}")

        return applied_changes

    except Exception as exc:
        logger.error("[GhostApply] Hata: %s", exc)
        return []
