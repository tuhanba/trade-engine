import config

def test_config_thresholds():
    """Eşik değerlerinin mantıksal sıralamasını kontrol et"""
    assert config.DATA_THRESHOLD < config.WATCHLIST_THRESHOLD
    assert config.WATCHLIST_THRESHOLD < config.TELEGRAM_THRESHOLD
    assert config.TELEGRAM_THRESHOLD < config.TRADE_THRESHOLD

def test_config_risk_params():
    """Risk parametrelerinin geçerli aralıklarda olduğunu kontrol et"""
    assert 0 < config.RISK_PCT <= 5.0
    assert config.MAX_LEVERAGE <= 125
    assert config.MAX_OPEN_TRADES > 0
    assert 0 < config.DAILY_MAX_LOSS_PCT <= 10.0

def test_config_tp_logic():
    """TP ve SL mantığını kontrol et"""
    assert config.TP1_R < config.TP2_R < config.TP3_R
    assert config.TP1_CLOSE_PCT + config.TP2_CLOSE_PCT + config.RUNNER_CLOSE_PCT == 100
