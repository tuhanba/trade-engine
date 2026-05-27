"""
core/metrics.py – Prometheus Metrics Exporter
=============================================
Kurumsal (Enterprise) düzeyde sistemi gözlemleyebilmek (Observability)
için Grafana/Prometheus tarafından çekilebilecek metrikleri tanımlar.
"""

import logging
from prometheus_client import start_http_server, Counter, Gauge

logger = logging.getLogger("ax.metrics")

# Metrik Tanımlamaları
# 1. Ticaret Metrikleri
TRADE_OPENED_COUNTER = Counter("trade_opened_total", "Toplam açılan işlem sayısı", ["symbol", "side"])
TRADE_CLOSED_COUNTER = Counter("trade_closed_total", "Toplam kapanan işlem sayısı", ["symbol", "side", "reason", "result"])
TOTAL_PNL_GAUGE = Gauge("total_paper_pnl", "Kümülatif Kâr/Zarar (USD)")

# 2. Performans Metrikleri
WEBSOCKET_PING_GAUGE = Gauge("websocket_ping_ms", "Binance/CCXT WebSocket Gecikmesi (ms)")
DB_LATENCY_GAUGE = Gauge("db_write_latency_ms", "Veritabanı yazma gecikmesi (ms)")

# 3. Sistem Sağlık Metrikleri
ERROR_COUNTER = Counter("system_errors_total", "Sistemdeki kritik hata sayısı", ["module"])
ACTIVE_TRADES_GAUGE = Gauge("active_trades_current", "Şu an açık olan trade sayısı")


def start_metrics_server(port: int = 8000):
    """Prometheus için localhost:port/metrics uç noktasını başlatır."""
    try:
        start_http_server(port)
        logger.info(f"Prometheus Metrics Server başlatıldı. Port: {port}")
    except Exception as e:
        logger.error(f"Prometheus Metrics Server başlatılamadı: {e}")
