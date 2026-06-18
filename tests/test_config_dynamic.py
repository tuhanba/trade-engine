"""
tests/test_config_dynamic.py
============================
Fix C REGRESYON TESTİ — Dinamik config çözümleyici (PEP 562 __getattr__) korunur.

Bug: bir modül `config.EXECUTION_MODE = "..."` diye DOĞRUDAN atama yaparsa statik
global yeniden yaratılır → __getattr__ o isim için o süreçte kalıcı ölür → DB'deki
tg_execution_mode değişse bile süreç eski (donmuş) değeri görür. Sonuç: Telegram
"live", Dashboard "paper" gösterir; Friday'in mod/human değişikliği engine'de
görünmez.

Fix: tüm doğrudan atamalar database.update_system_state() (set_state) + cache
pop'a çevrildi. Bu test (1) çözümleyicinin DB'den dinamik okuduğunu, (2) production
kodda doğrudan atama kalmadığını doğrular.
"""
import pathlib
import re

import config


def test_mode_resolves_from_db(test_db):
    """EXECUTION_MODE DB'den dinamik çözülmeli; statik gölge global olmamalı."""
    test_db.update_system_state("tg_execution_mode", "live")
    config._CONFIG_CACHE.pop("EXECUTION_MODE", None)
    assert getattr(config, "EXECUTION_MODE") == "live"
    assert "EXECUTION_MODE" not in config.__dict__, "statik gölge global var (resolver gölgelendi)"

    test_db.update_system_state("tg_execution_mode", "paper")
    config._CONFIG_CACHE.pop("EXECUTION_MODE", None)
    assert getattr(config, "EXECUTION_MODE") == "paper"


def test_human_mode_resolves_from_db(test_db):
    """HUMAN_MODE de DB'den dinamik çözülmeli; statik gölge olmamalı."""
    test_db.update_system_state("tg_human_mode", "True")
    config._CONFIG_CACHE.pop("HUMAN_MODE", None)
    assert getattr(config, "HUMAN_MODE") is True
    assert "HUMAN_MODE" not in config.__dict__


# Production (uzun-ömürlü runtime) kodda DOĞRUDAN atama (config.X=.. / _cfg.X=..) yasak.
# `==` karşılaştırmaları (okuma) hariç tutmak için `=(?!=)` negatif-ileri-bakış.
_ASSIGN_RE = re.compile(r"\.(EXECUTION_MODE|HUMAN_MODE)\s*=(?!=)")
# Hariç tutulanlar:
#   tests/ + test_*.py + conftest.py → izole süreçte bilinçli kurulum yapabilir.
#   scripts/ → tek-süreçlik backtest/migration harness'ları; zaten DB'ye
#     update_system_state ile yazıp save/restore yaparlar. Fix C bug'ı çok-süreçli
#     runtime'ın (engine + dashboard) mod tutarsızlığıdır; harness'ları kapsamaz.
#   archive/, scratch/ → arşiv/deneme.
_EXCLUDE_DIRS = {"tests", "scripts", "archive", "scratch", "backtest_data", "monitoring", ".git"}


def _production_py_files():
    root = pathlib.Path(__file__).resolve().parent.parent
    for p in root.rglob("*.py"):
        rel = p.relative_to(root)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.name.startswith("test_") or p.name == "conftest.py":
            continue
        yield p


def test_no_direct_config_assignment_in_production():
    """config.EXECUTION_MODE/HUMAN_MODE = ... doğrudan atama production'da KALMAMALI."""
    hits = []
    for p in _production_py_files():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _ASSIGN_RE.search(line):
                hits.append(f"{p}:{i}: {line.strip()}")
    assert not hits, "Doğrudan dinamik-config ataması kaldı:\n" + "\n".join(hits)
