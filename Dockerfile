# Dockerfile - Aurvex Trade Engine (Enterprise)
FROM python:3.11-slim

# Çalışma dizinini ayarla
WORKDIR /app

# Gerekli sistem paketlerini kur (build-essential, SQLite vb. için)
RUN apt-get update && apt-get install -y \
    build-essential \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Gereksinimleri kopyala ve kur
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodlarını kopyala
COPY . .

# Prometheus metrik portu
EXPOSE 8000
# Dashboard Flask portu
EXPOSE 5000

# Varsayılan başlatma komutu (docker-compose ezecek)
CMD ["python", "async_scalp_engine.py"]
