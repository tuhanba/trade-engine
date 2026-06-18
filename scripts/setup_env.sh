#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AX Trade Engine — tek komutluk geliştirme ortamı kurulumu (idempotent)
# Kullanım:  bash scripts/setup_env.sh [--no-tests]
# NEDEN: PEP-668 sistemlerinde bare `pip install` "externally-managed"
#        hatasi verir; izole bir .venv sart. Yeni bir klon/oturumun
#        "her seyi duzgun calistirmasi" icin tek giris noktasi.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

RUN_TESTS=1
[[ "${1:-}" == "--no-tests" ]] && RUN_TESTS=0

# Script nereden cagrilirsa cagrilsin repo kokune gec.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

PY="${PYTHON:-python3}"
VENV=".venv"

echo "==> [1/5] Sanal ortam ($VENV)"
if [[ ! -d "$VENV" ]]; then
  "$PY" -m venv "$VENV"
  echo "    olusturuldu"
else
  echo "    mevcut, atlandi"
fi
VPY="$VENV/bin/python"   # bundan sonra hep venv'in python'u

echo "==> [2/5] pip guncelle + bagimliliklar"
"$VPY" -m pip install --upgrade pip >/dev/null
"$VPY" -m pip install -r requirements.txt

echo "==> [3/5] .env"
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  echo "    .env olusturuldu (.env.example'dan) — anahtarlari doldurun"
else
  echo "    .env mevcut, korundu"
fi

echo "==> [4/5] Veritabani (init_db)"
"$VPY" -c "from database import init_db; init_db()"
echo "    init_db tamam"

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "==> [5/5] Test paketi (pytest)"
  "$VPY" -m pytest tests/ -q
else
  echo "==> [5/5] Testler atlandi (--no-tests)"
fi

echo
echo "Kurulum tamam. Calistirmak icin:"
echo "   source $VENV/bin/activate"
echo "   python async_scalp_engine.py    # engine (trade acan surec)"
echo "   python app.py                   # dashboard (:5000)"
