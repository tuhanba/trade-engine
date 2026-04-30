"""
AURVEX.Ai — Live Tracker
Açık trade'lerin anlık tick verilerini kaydeder ve kapanışta
post-trade analiz özeti üretir.
"""
import threading
import time
import sqlite3
import logging
from datetime import datetime, timezone

from config import DB_PATH

logger = logging.getLogger(__name__)

TICK_INTERVAL = 15  # saniye


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tracker_tables():
    """trade_ticks ve trade_analysis tablolarını oluştur."""
    conn = _db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_ticks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT,
            trade_id    INTEGER,
            ts          TEXT,
            price       REAL,
            rsi         REAL,
            macd_hist   REAL,
            funding     REAL,
            direction   TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_analysis (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol          TEXT,
            trade_id        INTEGER,
            entry           REAL,
            exit_price      REAL,
            direction       TEXT,
            actual_rr       REAL,
            max_rr_reached  REAL,
            rr_left         REAL,
            duration_min    REAL,
            insight         TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def save_analysis(analysis):
    """Analiz sonucunu DB'ye kaydet."""
    if not analysis:
        return
    try:
        conn = _db()
        conn.execute("""
            INSERT INTO trade_analysis
                (symbol, trade_id, entry, exit_price, direction,
                 actual_rr, max_rr_reached, rr_left, duration_min, insight)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            analysis.get("symbol", ""),
            analysis.get("trade_id", 0),
            analysis.get("entry", 0),
            analysis.get("exit_price", 0),
            analysis.get("direction", ""),
            analysis.get("actual_rr", 0),
            analysis.get("max_rr_reached", 0),
            analysis.get("rr_left_on_table", 0),
            analysis.get("duration_min", 0),
            analysis.get("insight", ""),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_analysis hata: {e}")


class LiveTracker:
    """
    Açık trade'lerin anlık fiyat/indikatör verilerini takip eder.
    Her TICK_INTERVAL saniyede bir aktif trade'leri günceller.
    """

    def __init__(self, client):
        self.client      = client
        self._trades     = {}          # symbol → trade dict
        self._lock       = threading.Lock()
        self._running    = False
        self._thread     = None
        self._max_fav    = {}          # symbol → en iyi fiyat (MFE takibi)

    # ─── Public API ────────────────────────────────────────────

    def add_trade(self, symbol, trade_dict):
        """Yeni açılan trade'i takibe al."""
        with self._lock:
            self._trades[symbol] = dict(trade_dict)
            self._max_fav[symbol] = trade_dict.get("entry", 0)
            logger.debug(f"[Tracker] {symbol} takibe alındı.")

    def remove_trade(self, symbol, exit_price):
        """
        Trade'i takipten çıkar ve analiz özeti döndür.
        scalp_bot.py bu değeri kullanarak actual_rr / max_rr gösterir.
        """
        with self._lock:
            trade = self._trades.pop(symbol, None)
            max_fav = self._max_fav.pop(symbol, exit_price)

        if not trade:
            return {}

        entry     = trade.get("entry", 0) or 0
        direction = trade.get("direction", "LONG")
        sl        = trade.get("sl", 0) or 0
        tp        = trade.get("tp", 0) or 0
        open_time = trade.get("open_time")
        trade_id  = trade.get("trade_id", 0)

        sl_dist = abs(entry - sl) if sl and entry else 1e-10

        if direction == "LONG":
            actual_move  = exit_price - entry
            max_move     = max_fav - entry
        else:
            actual_move  = entry - exit_price
            max_move     = entry - max_fav

        actual_rr     = round(actual_move  / sl_dist, 3) if sl_dist else 0
        max_rr        = round(max_move     / sl_dist, 3) if sl_dist else 0
        rr_left       = round(max(0, max_rr - actual_rr), 3)

        duration_min = 0
        if open_time:
            try:
                now = datetime.now(timezone.utc)
                if open_time.tzinfo is None:
                    open_time = open_time.replace(tzinfo=timezone.utc)
                duration_min = round((now - open_time).total_seconds() / 60, 1)
            except Exception:
                pass

        # Basit insight
        if actual_rr >= 1.0:
            insight = f"En iyi çıkış {max_fav:.5f} zamanındaydı - {rr_left:.2f}R masada kaldı"
        elif actual_rr < 0:
            insight = f"SL'e vurdu. Max lehimize hareket: {max_rr:.2f}R"
        else:
            insight = f"Kısmi kazanç. Max RR: {max_rr:.2f}R"

        # Funding rate bilgisi ekle (varsa)
        try:
            fr = self.client.futures_funding_rate(symbol=symbol, limit=1)
            if fr:
                rate_pct = float(fr[0]["fundingRate"]) * 100
                insight += f" | Funding rate %{rate_pct:.1f} oraninda {'leh' if (direction=='LONG' and rate_pct<0) or (direction=='SHORT' and rate_pct>0) else 'aley'}"
        except Exception:
            pass

        analysis = {
            "symbol":          symbol,
            "trade_id":        trade_id,
            "entry":           entry,
            "exit_price":      exit_price,
            "direction":       direction,
            "actual_rr":       actual_rr,
            "max_rr_reached":  max_rr,
            "rr_left_on_table": rr_left,
            "duration_min":    duration_min,
            "insight":         insight,
        }

        return analysis

    def start(self):
        """Arka plan tick thread'ini başlat."""
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("[Tracker] Başlatıldı.")

    def stop(self):
        """Arka plan thread'ini durdur."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[Tracker] Durduruldu.")

    # ─── Arka Plan Döngüsü ─────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f"[Tracker] Tick hata: {e}")
            time.sleep(TICK_INTERVAL)

    def _tick(self):
        """Her TICK_INTERVAL'da aktif trade'lerin MFE'sini güncelle."""
        with self._lock:
            symbols = list(self._trades.keys())

        for symbol in symbols:
            try:
                ticker = self.client.get_symbol_ticker(symbol=symbol)
                price  = float(ticker["price"])

                with self._lock:
                    if symbol not in self._trades:
                        continue
                    direction = self._trades[symbol].get("direction", "LONG")
                    entry     = self._trades[symbol].get("entry", price)

                    # MFE takibi
                    cur_max = self._max_fav.get(symbol, entry)
                    if direction == "LONG":
                        self._max_fav[symbol] = max(cur_max, price)
                    else:
                        self._max_fav[symbol] = min(cur_max, price) if cur_max != entry else price

                # DB'ye tick kaydet
                conn = _db()
                conn.execute("""
                    INSERT INTO trade_ticks (symbol, trade_id, ts, price, direction)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    symbol,
                    self._trades.get(symbol, {}).get("trade_id", 0) if symbol in self._trades else 0,
                    datetime.now(timezone.utc).isoformat(),
                    price,
                    direction,
                ))
                conn.commit()
                conn.close()

            except Exception:
                pass
