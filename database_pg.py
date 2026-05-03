"""
PostgreSQL Database Adapter v1.0
SQLite'dan PostgreSQL'e geçiş için hazırlık katmanı.
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import logging

logger = logging.getLogger(__name__)

# Çevresel değişkenlerden bağlantı bilgilerini al
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_NAME = os.getenv("PG_NAME", "trade_engine")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS", "password")

class PGDatabase:
    def __init__(self):
        self.conn_params = {
            "host": PG_HOST,
            "port": PG_PORT,
            "dbname": PG_NAME,
            "user": PG_USER,
            "password": PG_PASS
        }

    def get_conn(self):
        try:
            conn = psycopg2.connect(**self.conn_params)
            return conn
        except Exception as e:
            logger.error(f"PostgreSQL bağlantı hatası: {e}")
            return None

    def init_db(self):
        """Tabloları oluştur (SQLite şemasına sadık kalarak)."""
        conn = self.get_conn()
        if not conn: return
        
        try:
            with conn.cursor() as cur:
                # Örnek tablo oluşturma (diğerleri eklenecek)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS trades (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR(20),
                        direction VARCHAR(10),
                        entry FLOAT,
                        sl FLOAT,
                        tp1 FLOAT,
                        qty FLOAT,
                        status VARCHAR(20),
                        open_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        close_time TIMESTAMP,
                        net_pnl FLOAT DEFAULT 0
                    )
                """)
                conn.commit()
                logger.info("PostgreSQL tabloları hazır.")
        except Exception as e:
            logger.error(f"PG Init hatası: {e}")
        finally:
            conn.close()

# Singleton instance
pg_db = PGDatabase()
