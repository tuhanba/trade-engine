"""
elite_monitor.py — AX Elite Performance & Cleanup Monitor
=========================================================
Görevleri:
  - Veritabanı boyutunu kontrol eder ve eski logları temizler.
  - Sistem kaynaklarını (CPU/RAM) izler.
  - Ghost Trading verilerinin sağlığını kontrol eder.
  - Telegram üzerinden haftalık performans raporu hazırlar.
"""
import os
import sqlite3
import logging
import psutil
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class EliteMonitor:
    def __init__(self, db_path):
        self.db_path = db_path

    def auto_cleanup(self, days_to_keep=7):
        """Eski logları ve geçici verileri temizler."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d %H:%M:%S')
                
                # Eski AI loglarını temizle
                cursor.execute("DELETE FROM ai_logs WHERE created_at < ?", (cutoff_date,))
                # Eski aday sinyalleri temizle (Ghost Trading verileri hariç)
                cursor.execute("DELETE FROM signal_candidates WHERE created_at < ? AND decision NOT IN ('ALLOW', 'WATCH')", (cutoff_date,))
                
                conn.commit()
                logger.info(f"🧹 Auto-Cleanup: {days_to_keep} günden eski veriler temizlendi.")
        except Exception as e:
            logger.error(f"Cleanup hatası: {e}")

    def get_system_health(self):
        """Sistem sağlık durumunu döner."""
        cpu_usage = psutil.cpu_percent(interval=1)
        ram_usage = psutil.virtual_memory().percent
        db_size_mb = os.path.getsize(self.db_path) / (1024 * 1024)
        
        status = "HEALTHY" if cpu_usage < 80 and ram_usage < 85 else "STRESSED"
        
        return {
            "status": status,
            "cpu": f"%{cpu_usage}",
            "ram": f"%{ram_usage}",
            "db_size": f"{db_size_mb:.2f} MB"
        }

    def generate_elite_report(self):
        """Haftalık Elite performans raporu özeti."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Son 7 gündeki toplam sinyal ve başarı oranı
                cursor.execute("SELECT COUNT(*), SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END) FROM signal_candidates WHERE created_at > date('now', '-7 days')")
                total, allowed = cursor.fetchone()
                
                return f"📊 *Elite Haftalık Rapor*\n- Toplam Sinyal: {total}\n- Onaylanan: {allowed}\n- Sistem Durumu: {self.get_system_health()['status']}"
        except:
            return "Rapor hazırlanamadı."
