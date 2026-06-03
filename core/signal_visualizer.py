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


def generate_heatmap_image_bytes(days: int = 30) -> Optional[bytes]:
    """
    Kapatılan işlemlerin PnL dağılımını saat bazında gruplayıp
    görsel bir ısı haritası grafiği (PNG bytes) üretir.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        import database
    except ImportError:
        return None

    try:
        # 1. Veriyi çek
        with database.get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol,
                       strftime('%H', close_time) AS hour,
                       SUM(net_pnl) AS total_pnl
                FROM trades
                WHERE status = 'closed' AND close_time >= datetime('now', ?)
                GROUP BY symbol, hour
            """, (f"-{days} days",)).fetchall()
            
        if not rows:
            return None
            
        # 2. Matris yapısını oluştur
        symbols = sorted(list(set(row[0] for row in rows)))
        hours = [f"{h:02d}" for h in range(24)]
        symbol_idx = {sym: idx for idx, sym in enumerate(symbols)}
        
        grid = np.zeros((len(symbols), 24))
        for row in rows:
            sym, hr_str, pnl = row[0], row[1], float(row[2] or 0)
            try:
                hr = int(hr_str)
                grid[symbol_idx[sym], hr] = pnl
            except Exception:
                pass

        # 3. Matplotlib ile çiz
        plt.style.use('dark_background')
        fig_height = max(4.0, len(symbols) * 0.4 + 1.8)
        fig, ax = plt.subplots(figsize=(10, fig_height), dpi=150)
        
        # Özel Red-Black-Green Colormap
        from matplotlib.colors import LinearSegmentedColormap
        colors = ["#ff1744", "#181818", "#00e676"]
        cmap = LinearSegmentedColormap.from_list("pnl_map", colors)
        
        vmax = max(1.0, np.max(np.abs(grid)))
        im = ax.imshow(grid, cmap=cmap, aspect='auto', vmin=-vmax, vmax=vmax)
        
        ax.set_xticks(np.arange(24))
        ax.set_xticklabels(hours, fontsize=8)
        ax.set_yticks(np.arange(len(symbols)))
        ax.set_yticklabels(symbols, fontsize=8, fontweight='bold')
        
        ax.set_xlabel("Saat (UTC)", fontsize=9, labelpad=8)
        ax.set_ylabel("Sembol", fontsize=9)
        ax.set_title(f"Portföy Isı Haritası (Son {days} Gün PnL)", fontsize=11, fontweight='bold', pad=15)
        
        # Hücre içi değerleri yaz
        for i in range(len(symbols)):
            for j in range(24):
                val = grid[i, j]
                if abs(val) > 0.01:
                    text_color = "black" if abs(val) > vmax * 0.4 else "white"
                    ax.text(j, i, f"{val:+.1f}", ha="center", va="center", color=text_color, fontsize=6, fontweight='bold')

        # Izgara çizgileri
        ax.set_xticks(np.arange(24) - 0.5, minor=True)
        ax.set_yticks(np.arange(len(symbols)) - 0.5, minor=True)
        ax.grid(which="minor", color="#2c2c2c", linestyle='-', linewidth=0.5)
        ax.tick_params(which="minor", size=0)
        
        cbar = fig.colorbar(im, ax=ax, orientation='horizontal', pad=0.08, shrink=0.5)
        cbar.ax.tick_params(labelsize=8)
        cbar.set_label("Toplam Net PnL ($)", fontsize=8)

        fig.patch.set_facecolor('#121212')
        ax.set_facecolor('#121212')
        
        import io
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
        buf.seek(0)
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        logger.error(f"[Visualizer] Isı haritası grafiği çizilirken hata: {e}")
        return None
