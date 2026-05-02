from core.market_scanner import MarketScanner


class _Client:
    def futures_ticker(self):
        return [{"symbol": "BTCUSDT", "quoteVolume": "30000000", "lastPrice": "60000", "priceChangePercent": "2.5"}]

    def futures_exchange_info(self):
        return {"symbols": [{"symbol": "BTCUSDT", "quoteAsset": "USDT", "status": "TRADING"}]}


def test_market_scanner_returns_tradeability_fields():
    scanner = MarketScanner(_Client(), db_path=":memory:")
    out = scanner.scan()
    if out:
        assert "volume_score" in out[0]
        assert "tradeability_score" in out[0]
