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


def calculate_correlation_matrix(symbols: list[str], interval: str = "1h", limit: int = 50) -> dict:
    """Açık pozisyon sembolleri arasındaki tam Pearson korelasyon matrisi (Faz 6.3).

    _get_returns altyapısını yeniden kullanır (yeniden hesaplamaz).
    Returns: {"symbols": [...], "matrix": [[float|None,...]], "max_pair": {...}}
    Köşegen 1.0; getirisi alınamayan sembol satır/sütununda None.
    """
    result = {"symbols": list(symbols), "matrix": [], "max_pair": None}
    if not symbols:
        return result

    # Getirileri bir kez çek (tekrar tekrar fetch'i önle)
    returns = {s: _get_returns(s, interval, limit) for s in symbols}
    n = len(symbols)
    matrix = [[None] * n for _ in range(n)]
    max_abs = -1.0
    max_pair = None

    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            r1, r2 = returns[symbols[i]], returns[symbols[j]]
            corr = None
            if r1 is not None and r2 is not None and len(r1) and len(r2):
                m = min(len(r1), len(r2))
                try:
                    c = np.corrcoef(r1[-m:], r2[-m:])[0, 1]
                    if not np.isnan(c):
                        corr = round(float(c), 3)
                except Exception:
                    corr = None
            matrix[i][j] = corr
            matrix[j][i] = corr
            if corr is not None and abs(corr) > max_abs:
                max_abs = abs(corr)
                max_pair = {"a": symbols[i], "b": symbols[j], "corr": corr}

    result["matrix"] = matrix
    result["max_pair"] = max_pair
    return result


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


def _fetch_db_stats_for_kelly(symbol: str) -> tuple[float, float]:
    """Fetches win rate and payoff ratio from the database for a symbol."""
    try:
        from database import get_conn
        with get_conn() as conn:
            # Let's try coin_profiles first
            row = conn.execute("SELECT win_rate, total_trades FROM coin_profiles WHERE symbol = ?", (symbol,)).fetchone()
            if row and row[1] >= 5:
                wr = float(row[0] or 0.5)
                # For payoff ratio, query trades
                payoff_row = conn.execute("""
                    SELECT 
                        COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                        COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                    FROM trades 
                    WHERE status = 'closed' AND symbol = ?
                """, (symbol,)).fetchone()
                if payoff_row and payoff_row[1] > 0:
                    payoff = payoff_row[0] / payoff_row[1]
                else:
                    payoff = 2.0
                return wr, payoff
                
            # If coin profiles doesn't have enough, query global/trades
            row_global = conn.execute("""
                SELECT 
                    COUNT(*),
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                    COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                    COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                FROM trades 
                WHERE status = 'closed'
            """).fetchone()
            if row_global and row_global[0] >= 5:
                total = row_global[0]
                wins = row_global[1]
                avg_win = row_global[2]
                avg_loss = row_global[3]
                wr = wins / total
                payoff = avg_win / avg_loss if avg_loss > 0 else 2.0
                return wr, payoff
    except Exception as e:
        logger.warning(f"Error fetching DB stats for Kelly: {e}")
    # Default fallback
    return 0.50, 2.0


def calculate_multi_asset_kelly(
    symbols: list[str],
    win_rates: dict[str, float] = None,
    payoff_ratios: dict[str, float] = None,
    interval: str = "1h",
    limit: int = 50,
    half_kelly: bool = True
) -> dict[str, float]:
    """
    Multi-Asset Kelly Matrix: f* = C^-1 * m
    Where C is the Pearson correlation matrix (regularized),
    and m is the expected excess return vector m_i = p_i * R_i - (1 - p_i).
    """
    if not symbols:
        return {}
    
    # 1. Calculate the Pearson correlation matrix
    corr_data = calculate_correlation_matrix(symbols, interval, limit)
    symbols_resolved = corr_data["symbols"]
    n = len(symbols_resolved)
    if n == 0:
        return {}
        
    # Build the numpy correlation matrix, default missing correlations to 0
    C = np.zeros((n, n))
    for i in range(n):
        C[i, i] = 1.0
        for j in range(n):
            if i != j:
                val = corr_data["matrix"][i][j]
                C[i, j] = val if val is not None else 0.0
                
    # 2. Regularization (Shrinkage: C_reg = (1 - lam) * C + lam * I)
    lam = float(getattr(config, "KELLY_REGULARIZATION", 0.15))
    C_reg = (1.0 - lam) * C + lam * np.eye(n)
    
    # 3. Calculate expected return vector m
    m = np.zeros(n)
    for i, sym in enumerate(symbols_resolved):
        p = (win_rates or {}).get(sym)
        r = (payoff_ratios or {}).get(sym)
        
        # If not provided, fetch from DB
        if p is None or r is None:
            db_p, db_r = _fetch_db_stats_for_kelly(sym)
            if p is None: p = db_p
            if r is None: r = db_r
            
        m[i] = p * r - (1.0 - p)
        
    # 4. Solve f* = C_reg^-1 * m
    try:
        f_star = np.linalg.solve(C_reg, m)
    except Exception as e:
        logger.warning(f"Failed to solve Kelly matrix: {e}. Falling back to single-asset Kelly.")
        f_star = m
        
    # 5. Apply Half-Kelly and clamp weights
    scale = 0.5 if half_kelly else 1.0
    weights = {}
    for i, sym in enumerate(symbols_resolved):
        w = f_star[i] * scale
        # Clamp between 0.005 and 0.03 (which matches [0.5%, 3.0%] in percentage)
        weights[sym] = max(0.005, min(0.03, float(w)))
        
    return weights
