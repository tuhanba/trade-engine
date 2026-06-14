# AurvexAI Monitoring (Faz 6.6)

Prometheus + Grafana ile **sistem/altyapı gözlemlenebilirliği**. Trading UI'a
dokunulmaz — bu katman yalnızca engine'in `:8000/metrics` Prometheus çıktısını
görselleştirir.

## Çalıştırma

```bash
docker compose up -d prometheus grafana
```

- **Prometheus:** http://localhost:9090 — `engine:8000` 15 sn'de bir kazınır.
- **Grafana:** http://localhost:3000 — varsayılan kullanıcı `admin` / parola `aurvex`
  (env ile değiştir: `GRAFANA_USER`, `GRAFANA_PASSWORD`).
  - "AurvexAI" klasöründe **AurvexAI — Sistem Metrikleri** dashboard'u otomatik
    provision edilir (datasource + dashboard hazır gelir, elle kurulum yok).

## İzlenen metrikler (core/metrics.py)

| Metrik | Açıklama |
|---|---|
| `total_paper_pnl` | Kümülatif PnL (USD) |
| `active_trades_current` | Açık trade sayısı |
| `websocket_ping_ms` | WS gecikmesi |
| `db_write_latency_ms` | DB yazma gecikmesi |
| `trade_opened_total` / `trade_closed_total` | İşlem sayaçları (label'lı) |
| `system_errors_total` | Modül bazlı hata sayacı |

## Dosyalar
- `prometheus.yml` — scrape config
- `grafana/provisioning/` — datasource + dashboard provider (otomatik)
- `grafana/dashboards/aurvex_system.json` — hazır sistem dashboard'u
