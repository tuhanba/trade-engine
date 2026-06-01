import io
import logging
import pandas as pd
from typing import Optional

logger = logging.getLogger("ax.visualizer")

def generate_chart_bytes(symbol: str, entry: float, sl: float,
                         tp1: Optional[float] = None,
                         tp2: Optional[float] = None,
                         tp3: Optional[float] = None,
                         direction: str = "LONG",
                         client = None) -> Optional[bytes]:
    """
    matplotlib kullanarak sembolün son fiyat hareketini ve
    Entry/SL/TP seviyelerini gösteren premium karanlık mod grafik PNG byte'larını üretir.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend to avoid GUI threads
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib kurulu değil. Grafik oluşturma atlandı.")
        return None

    if client is None:
        return None

    try:
        # Son 40 mumu (5m) çek
        klines = client.futures_klines(symbol=symbol, interval="5m", limit=40)
        if not klines or len(klines) < 10:
            return None
        
        df = pd.DataFrame(klines, columns=[
            "time", "open", "high", "low", "close", "volume",
            "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
        ])
        closes = df["close"].astype(float).tolist()
        times = list(range(len(closes)))

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(6, 3.5), dpi=150)
        
        # Premium Area Chart
        ax.plot(times, closes, color="#00e5ff", linewidth=2, label="Price")
        ax.fill_between(times, closes, min(closes) * 0.999, color="#00e5ff", alpha=0.1)

        # Seviye çizgilerini çiz
        ax.axhline(y=entry, color="#00e676", linestyle="--", linewidth=1.5, label=f"Entry: {entry}")
        ax.axhline(y=sl, color="#ff1744", linestyle="--", linewidth=1.5, label=f"SL: {sl}")
        
        if tp1 and tp1 > 0:
            ax.axhline(y=tp1, color="#ffd600", linestyle="--", linewidth=1.2, label=f"TP1: {tp1}")
        if tp2 and tp2 > 0:
            ax.axhline(y=tp2, color="#ff9100", linestyle="--", linewidth=1.2, label=f"TP2: {tp2}")
        if tp3 and tp3 > 0:
            ax.axhline(y=tp3, color="#2979ff", linestyle="--", linewidth=1.2, label=f"TP3: {tp3}")

        ax.set_title(f"{symbol} ({direction}) 5m Grafik", fontsize=10, fontweight='bold', color="#ffffff", pad=10)
        ax.set_facecolor('#121212')
        fig.patch.set_facecolor('#121212')
        
        # Grid ve eksen ayarları
        ax.grid(True, color='#2c2c2c', linestyle=':', linewidth=0.5)
        ax.tick_params(colors='#888888', labelsize=8)
        
        # Sağ ve üst sınır çizgilerini gizle
        for spine in ["top", "right"]:
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color("#2c2c2c")
        ax.spines["bottom"].set_color("#2c2c2c")

        # PNG olarak byte stream'e kaydet
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"[Visualizer] {symbol} grafiği oluşturulurken hata: {e}")
        return None
