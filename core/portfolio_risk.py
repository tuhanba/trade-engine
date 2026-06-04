"""
core/portfolio_risk.py - Phase G Portfolio Risk & Quant Guard Module
=====================================================================
Provides correlation blocking, Value-at-Risk (VaR) limits, and Sharpe/Sortino metrics.
"""

import logging
import math
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Optional, Any

import config
import database
from core.market_data import get_klines

logger = logging.getLogger("ax.portfolio_risk")


def _get_returns(symbol: str, interval: str = "1h", limit: int = 50) -> Optional[np.ndarray]:
    """Mum verilerini çekip basit getiri serisi döner."""
    try:
        klines = get_klines(symbol, interval, limit)
        if not klines or len(klines) < 10:
            return None
        # Kapanış fiyatları (5. eleman)
        closes = np.array([float(k[4]) for k in klines])
        returns = np.diff(closes) / closes[:-1]
        return returns
    except Exception as e:
        logger.warning(f"Failed to calculate returns for {symbol}: {e}")
        return None


def calculate_max_correlation(open_symbols: list[str], new_symbol: str, interval: str = "1h", limit: int = 50) -> float:
    """
    Açık pozisyonlardaki semboller ile yeni sembol arasındaki maksimum Pearson korelasyonunu hesaplar.
    Döner: -1.0 ile 1.0 arası bir korelasyon değeri (veri yoksa 0.0).
    """
    if not open_symbols:
        return 0.0

    new_returns = _get_returns(new_symbol, interval, limit)
    if new_returns is None or len(new_returns) == 0:
        return 0.0

    max_corr = 0.0
    for open_sym in open_symbols:
        if open_sym == new_symbol:
            return 1.0
        open_returns = _get_returns(open_sym, interval, limit)
        if open_returns is None or len(open_returns) == 0:
            continue
            
        # Boyutları eşitle
        min_len = min(len(new_returns), len(open_returns))
        r1 = new_returns[-min_len:]
        r2 = open_returns[-min_len:]
        
        try:
            # Pearson Korelasyon Katsayısı
            corr = np.corrcoef(r1, r2)[0, 1]
            if not np.isnan(corr):
                max_corr = max(max_corr, corr)
        except Exception:
            pass

    return max_corr


def calculate_portfolio_var(
    open_positions: list[dict], 
    new_position: dict, 
    balance: float, 
    interval: str = "1h", 
    limit: int = 50
) -> float:
    """
    Tüm açık pozisyonlar ve yeni pozisyon adayının birleşiminden oluşan portföyün
    Parametrik Value-at-Risk (VaR %99) değerini hesaplar.
    Döner: VaR değeri (Hesap bakiyesinin yüzdesi olarak, örn. 0.03 = %3 VaR).
    """
    all_positions = list(open_positions) + [new_position]
    if not all_positions:
        return 0.0

    symbols = list(set(pos["symbol"] for pos in all_positions))
    returns_dict = {}
    
    for sym in symbols:
        ret = _get_returns(sym, interval, limit)
        if ret is not None and len(ret) > 5:
            returns_dict[sym] = ret

    if not returns_dict:
        return 0.0

    # Minimum ortak uzunluğu bulup serileri hizala
    min_len = min(len(ret) for ret in returns_dict.values())
    hizalanmis_returns = {sym: ret[-min_len:] for sym, ret in returns_dict.items()}
    
    try:
        df_returns = pd.DataFrame(hizalanmis_returns)
        # Kovaryans Matrisi
        cov_matrix = df_returns.cov().values
        
        # Ağırlıklar vektörü (Büyüklük USD / Toplam Bakiye)
        weights = []
        for sym in df_returns.columns:
            # Sembole ait toplam pozisyon büyüklüğü
            sym_notional = sum(
                float(pos.get("qty", 0) or pos.get("quantity", 0)) * float(pos.get("entry_price") or pos.get("entry", 0))
                for pos in all_positions if pos["symbol"] == sym
            )
            w = sym_notional / max(balance, 1.0)
            weights.append(w)
            
        w_vec = np.array(weights)
        
        # Portföy Varyansı: W^T * Cov * W
        port_variance = np.dot(w_vec.T, np.dot(cov_matrix, w_vec))
        port_volatility = math.sqrt(max(port_variance, 0.0))
        
        # Parametrik VaR %99 Güven Seviyesi -> Z = 2.33
        var_99 = 2.33 * port_volatility
        return var_99
    except Exception as e:
        logger.warning(f"Error calculating portfolio VaR: {e}")
        return 0.0


def calculate_sharpe_sortino_ratios(environment: str = "paper") -> dict:
    """
    Geçmiş günlük kapalı PnL getirilerinden Sharpe ve Sortino rasyolarını hesaplar.
    Döner: {"sharpe_ratio": float, "sortino_ratio": float}
    """
    try:
        init_balance = getattr(config, "INITIAL_PAPER_BALANCE", 2000.0)
        with database.get_conn() as conn:
            # Günlük PnL serisi
            rows = conn.execute("""
                SELECT DATE(close_time) as close_date,
                       SUM(net_pnl) as daily_pnl
                FROM trades
                WHERE status = 'closed' AND is_valid_for_stats = 1 AND environment = ?
                GROUP BY DATE(close_time)
                ORDER BY close_date
            """, (environment,)).fetchall()
            
        if len(rows) < 3:
            return {"sharpe_ratio": 0.0, "sortino_ratio": 0.0}

        daily_returns = [float(row["daily_pnl"]) / init_balance for row in rows]
        mean_ret = np.mean(daily_returns)
        std_ret = np.std(daily_returns)

        # Risk-free rate (günlük bazda sıfır kabul ediyoruz)
        # Sharpe Ratio (Yıllıklandırılmış: günlük Sharpe * sqrt(365))
        sharpe = (mean_ret / std_ret * math.sqrt(365)) if std_ret > 0 else 0.0

        # Sortino Ratio (Downside stdev hesaplanır)
        downside_diffs = [min(r, 0.0)**2 for r in daily_returns]
        downside_std = math.sqrt(np.mean(downside_diffs))
        if downside_std > 0:
            sortino = (mean_ret / downside_std * math.sqrt(365))
        else:
            sortino = 99.99 if mean_ret > 0 else 0.0

        return {
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2)
        }
    except Exception as e:
        logger.error(f"Sharpe/Sortino calculation failed: {e}")
        return {"sharpe_ratio": 0.0, "sortino_ratio": 0.0}
