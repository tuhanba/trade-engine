import sqlite3
import psycopg2
import psycopg2.extras
import logging
import os
import sys

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('migrate_db')

SQLITE_DB = 'trading.db'
# Make sure these match config.py POSTGRES defaults
PG_HOST = os.getenv('POSTGRES_HOST', 'localhost')  # For running locally outside docker
PG_PORT = int(os.getenv('POSTGRES_PORT', 5432))
PG_DB = os.getenv('POSTGRES_DB', 'aurvex')
PG_USER = os.getenv('POSTGRES_USER', 'aurvex')
PG_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'aurvexpass')

def migrate():
    if not os.path.exists(SQLITE_DB):
        logger.error(f"SQLite veritabanı {SQLITE_DB} bulunamadı!")
        return

    logger.info(f"SQLite veritabanına bağlanılıyor: {SQLITE_DB}")
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row
    sqlite_cur = sqlite_conn.cursor()

    logger.info(f"PostgreSQL veritabanına bağlanılıyor: {PG_HOST}:{PG_PORT}/{PG_DB}")
    try:
        pg_conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD
        )
        pg_cur = pg_conn.cursor()
    except Exception as e:
        logger.error(f"PostgreSQL bağlantısı başarısız: {e}")
        return

    # Önce tüm tabloları getir
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row['name'] for row in sqlite_cur.fetchall()]
    
    # Exclude internal tables
    tables = [t for t in tables if not t.startswith('sqlite_')]
    logger.info(f"Taşınacak tablolar: {tables}")

    for table in tables:
        logger.info(f"[{table}] Tablo okunuyor...")
        
        sqlite_cur.execute(f"SELECT * FROM {table}")
        rows = sqlite_cur.fetchall()
        
        if not rows:
            logger.info(f"[{table}] Tablo boş, atlanıyor.")
            continue
            
        columns = rows[0].keys()
        col_names = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        
        # Insert or Ignore in Postgres
        # We need a conflict target. Let's just use regular insert since the target DB should be empty
        # but to be safe, if ID exists, we can use id as conflict target if it exists
        if 'id' in columns:
            insert_query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING"
        else:
            insert_query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
            
        data_to_insert = [tuple(row[col] for col in columns) for row in rows]
        
        logger.info(f"[{table}] {len(data_to_insert)} satır PostgreSQL'e yazılıyor...")
        try:
            pg_cur.executemany(insert_query, data_to_insert)
            pg_conn.commit()
            
            # Reset sequence if id column exists (Serial)
            if 'id' in columns:
                pg_cur.execute(f"SELECT setval('{table}_id_seq', COALESCE((SELECT MAX(id)+1 FROM {table}), 1), false);")
                pg_conn.commit()
                
            logger.info(f"[{table}] Başarıyla taşındı.")
        except Exception as e:
            pg_conn.rollback()
            logger.error(f"[{table}] Yazma hatası: {e}")

    logger.info("Tüm tabloların aktarımı tamamlandı!")
    
    sqlite_cur.close()
    sqlite_conn.close()
    pg_cur.close()
    pg_conn.close()

if __name__ == '__main__':
    migrate()
