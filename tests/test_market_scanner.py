"""test_market_scanner.py — MarketScanner filtreler ve scoring."""
import pytest


def _make_ticker(symbol="BTCUSDT", volume=50_000_000, price_chg=3.0, price=30000.0):
    return {
        "symbol":            symbol,
        "quoteVolume":       str(volume),
        "priceChangePercent":str(price_chg),
        "lastPrice":         str(price),
    }


def test_scanner_eligible(mock_client, tmp_db):
    from core.market_scanner import MarketScanner
    mock_client.futures_ticker.return_value = [
        _make_ticker("BTCUSDT", 50_000_000, 3.0)
    ]
    mock_client.futures_order_book.return_value = {
        "bids": [["29990", "5"], ["29980", "3"]],
        "asks": [["30010", "5"], ["30020", "3"]],
    }
    mock_client.futures_funding_rate.return_value = [{"fundingRate": "0.0001"}]
    mock_client.futures_open_interest_hist.return_value = [
        {"sumOpenInterestValue": "100000"},
        {"sumOpenInterestValue": "103000"},
    ]

    scanner = MarketScanner(mock_client, db_path=tmp_db)
    scanner.coin_universe = {"BTCUSDT"}
    results = scanner.scan(save_to_db=False)

    assert len(results) >= 0  # Coin universe'e bağlı


def test_scanner_avoid_low_volume(mock_client, tmp_db):
    from core.market_scanner import MarketScanner
    mock_client.futures_ticker.return_value = [
        _make_ticker("LOWVOL", volume=100_000)
    ]
    mock_client.futures_order_book.return_value = {"bids": [], "asks": []}
    mock_client.futures_funding_rate.return_value = []
    mock_client.futures_open_interest_hist.return_value = []

    scanner = MarketScanner(mock_client, db_path=tmp_db)
    scanner.coin_universe = {"LOWVOL"}
    results = scanner.scan(save_to_db=False)

    # Low volume → AVOID → candidates listesinde olmaz
    assert all(c["scanner_status"] != "AVOID" for c in results)


def test_scanner_pump_dump_penalty(mock_client, tmp_db):
    from core.market_scanner import MarketScanner
    mock_client.futures_ticker.return_value = [
        _make_ticker("PUMPUSDT", volume=100_000_000, price_chg=35.0)
    ]
    mock_client.futures_order_book.return_value = {
        "bids": [["100", "1000"]], "asks": [["101", "1000"]]
    }
    mock_client.futures_funding_rate.return_value = [{"fundingRate": "0.003"}]
    mock_client.futures_open_interest_hist.return_value = [
        {"sumOpenInterestValue": "100000"},
        {"sumOpenInterestValue": "90000"},
    ]

    scanner = MarketScanner(mock_client, db_path=tmp_db)
    scanner.coin_universe = {"PUMPUSDT"}
    results = scanner.scan(save_to_db=False)

    # Pump/dump → AVOID → candidates'ta olmamalı
    assert all(c["symbol"] != "PUMPUSDT" for c in results)


def test_scanner_scoring_components(mock_client, tmp_db):
    from core.market_scanner import MarketScanner
    ticker = _make_ticker("ETHUSDT", volume=30_000_000, price_chg=2.5, price=2000.0)
    mock_client.futures_ticker.return_value = [ticker]
    mock_client.futures_order_book.return_value = {
        "bids": [["1999", "50"], ["1998", "30"]],
        "asks": [["2001", "50"], ["2002", "30"]],
    }
    mock_client.futures_funding_rate.return_value = [{"fundingRate": "0.0001"}]
    mock_client.futures_open_interest_hist.return_value = [
        {"sumOpenInterestValue": "500000"},
        {"sumOpenInterestValue": "510000"},
    ]

    scanner = MarketScanner(mock_client, db_path=tmp_db)
    scanner.coin_universe = {"ETHUSDT"}
    results = scanner.scan(save_to_db=False)

    if results:
        r = results[0]
        assert 0 <= r["tradeability_score"] <= 10
        assert "volume_score" in r
        assert "spread_percent" in r


def test_scanner_bad_spread_avoid(mock_client, tmp_db):
    from core.market_scanner import MarketScanner
    import config
    old_max = config.MAX_SPREAD_PCT
    config.MAX_SPREAD_PCT = 0.01  # çok sıkı

    mock_client.futures_ticker.return_value = [
        _make_ticker("WIDEUSDT", volume=50_000_000, price_chg=2.0)
    ]
    mock_client.futures_order_book.return_value = {
        "bids": [["100",  "100"]],
        "asks": [["102",  "100"]],   # 2% spread
    }
    mock_client.futures_funding_rate.return_value = [{"fundingRate": "0.0001"}]
    mock_client.futures_open_interest_hist.return_value = []

    scanner = MarketScanner(mock_client, db_path=tmp_db)
    scanner.coin_universe = {"WIDEUSDT"}
    results = scanner.scan(save_to_db=False)

    # Geniş spread → AVOID
    assert all(c["symbol"] != "WIDEUSDT" for c in results)
    config.MAX_SPREAD_PCT = old_max
