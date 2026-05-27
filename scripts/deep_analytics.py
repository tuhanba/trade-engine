import sqlite3
import pandas as pd
import numpy as np
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trading.db")

def main():
    if not os.path.exists(DB_PATH):
        print("Database not found:", DB_PATH)
        return

    conn = sqlite3.connect(DB_PATH)
    
    print("=========================================================")
    print("             AX TRADE ENGINE - DEEP ANALYTICS            ")
    print("=========================================================")
    
    try:
        # 1. Analyze Actual Trades (Realized PnL)
        print("\n--- ACTUAL TRADES ANALYSIS ---")
        trades_query = """
        SELECT 
            symbol, direction, entry, sl, tp1, qty, leverage, 
            status, open_time, close_time, net_pnl, close_reason, metadata
        FROM trades 
        WHERE LOWER(status) = 'closed'
        """
        df_trades = pd.read_sql_query(trades_query, conn)
        
        if len(df_trades) > 0:
            df_trades['open_time'] = pd.to_datetime(df_trades['open_time'])
            df_trades['hour'] = df_trades['open_time'].dt.hour
            df_trades['day_of_week'] = df_trades['open_time'].dt.day_name()
            df_trades['is_win'] = df_trades['net_pnl'] > 0
            
            print(f"Total Closed Trades: {len(df_trades)}")
            print(f"Overall Win Rate: {df_trades['is_win'].mean()*100:.2f}%")
            print(f"Overall Net PnL: ${df_trades['net_pnl'].sum():.2f}")
            
            print("\n>> By Coin (Top 5 by Volume):")
            coin_stats = df_trades.groupby('symbol').agg(
                trades=('symbol', 'count'),
                win_rate=('is_win', 'mean'),
                pnl=('net_pnl', 'sum')
            ).sort_values('trades', ascending=False).head(5)
            for sym, row in coin_stats.iterrows():
                print(f"  {sym:8s} | Trades: {row['trades']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}% | PnL: ${row['pnl']:.2f}")

            print("\n>> By Hour of Day (Best 3 Hours):")
            hour_stats = df_trades.groupby('hour').agg(
                trades=('hour', 'count'),
                win_rate=('is_win', 'mean'),
                pnl=('net_pnl', 'sum')
            ).sort_values('pnl', ascending=False)
            
            for hr, row in hour_stats[hour_stats['trades'] >= 3].head(3).iterrows():
                print(f"  {hr:02.0f}:00 | Trades: {row['trades']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}% | PnL: ${row['pnl']:.2f}")

            print("\n>> By Direction:")
            dir_stats = df_trades.groupby('direction').agg(
                trades=('direction', 'count'),
                win_rate=('is_win', 'mean'),
                pnl=('net_pnl', 'sum')
            )
            for d, row in dir_stats.iterrows():
                print(f"  {d:5s} | Trades: {row['trades']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}% | PnL: ${row['pnl']:.2f}")
        else:
            print("No closed trades found in DB.")

        # 2. Analyze Ghost Signals (Machine Learning features)
        print("\n--- GHOST LEARNING (PATTERN ANALYSIS) ---")
        ghost_query = """
        SELECT 
            g.coin, g.direction, g.trigger_type, g.market_regime, g.final_score, g.confidence,
            r.virtual_outcome, r.virtual_pnl_r, r.virtual_mfe, r.virtual_mae, r.pattern_type
        FROM ghost_signals g
        JOIN ghost_results r ON g.id = r.ghost_id
        WHERE r.virtual_outcome IN ('WIN', 'LOSS')
        """
        try:
            df_ghost = pd.read_sql_query(ghost_query, conn)
            if len(df_ghost) > 0:
                df_ghost['is_win'] = df_ghost['virtual_outcome'] == 'WIN'
                
                print(f"Total Ghost Signals Analyzed: {len(df_ghost)}")
                print(f"Ghost Win Rate: {df_ghost['is_win'].mean()*100:.2f}%")
                
                print("\n>> Best Trigger Types (Min 3 samples):")
                trigger_stats = df_ghost.groupby('trigger_type').agg(
                    samples=('trigger_type', 'count'),
                    win_rate=('is_win', 'mean'),
                    avg_r=('virtual_pnl_r', 'mean')
                ).sort_values('avg_r', ascending=False)
                for trg, row in trigger_stats[trigger_stats['samples'] >= 3].head(5).iterrows():
                    print(f"  {trg:15s} | Samples: {row['samples']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}% | Avg R: {row['avg_r']:.2f}")

                print("\n>> By Market Regime:")
                regime_stats = df_ghost.groupby('market_regime').agg(
                    samples=('market_regime', 'count'),
                    win_rate=('is_win', 'mean'),
                    avg_r=('virtual_pnl_r', 'mean')
                ).sort_values('samples', ascending=False)
                for reg, row in regime_stats.iterrows():
                    reg_str = str(reg) if reg else "UNKNOWN"
                    print(f"  {reg_str:15s} | Samples: {row['samples']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}% | Avg R: {row['avg_r']:.2f}")
                
            else:
                print("No ghost results found.")
        except Exception as e:
            print("Could not analyze ghost signals:", e)

        # 3. Analyze Signal Candidates (Historical Signals)
        print("\n--- HISTORICAL SIGNALS ANALYSIS ---")
        sig_query = """
        SELECT symbol, direction, decision, status, ghost_pnl, setup_quality, final_score, market_regime, created_at 
        FROM signal_candidates
        """
        try:
            df_sig = pd.read_sql_query(sig_query, conn)
            if len(df_sig) > 0:
                print(f"Total historical signals found: {len(df_sig)}")
                df_sig['created_at'] = pd.to_datetime(df_sig['created_at'])
                
                df_resolved = df_sig[df_sig['ghost_pnl'].notna()].copy()
                if len(df_resolved) > 0:
                    df_resolved['is_win'] = df_resolved['ghost_pnl'] > 0
                    print(f"Total resolved signals (with PnL): {len(df_resolved)}")
                    print(f"Historical Win Rate: {df_resolved['is_win'].mean()*100:.2f}%")
                    
                    print("\n>> By Setup Quality:")
                    q_stats = df_resolved.groupby('setup_quality').agg(
                        count=('setup_quality', 'count'),
                        win_rate=('is_win', 'mean')
                    )
                    for q, row in q_stats.iterrows():
                        q_str = str(q) if q else "N/A"
                        print(f"  {q_str:3s} | Signals: {row['count']:3.0f} | Win Rate: {row['win_rate']*100:5.1f}%")
                else:
                    print("No PnL resolved signals found in historical data.")
            else:
                print("No historical signals found.")
        except Exception as e:
            print("Could not analyze historical signals:", e)

    finally:
        conn.close()

if __name__ == '__main__':
    main()
