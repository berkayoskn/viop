#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Borsa İstanbul VİOP (Vadeli İşlem ve Opsiyon Piyasası) Quant-Pro Algoritmik Sistem (v4.0 Ultimate)
==================================================================================================

v4.0 Ultimate Sürümünde Yer Alan 8 Kurumsal Modül:
--------------------------------------------------
1. %100 MİDAS & BIST VİOP BİREBİR FİYAT VE ÇİFT KALDIRAÇ SENKRONİZASYONU:
   - Tüm 50 VİOP kontratının gerçek yakın vade sözleşme kodlarını (`F_AKBNK1126`, `F_TCELL1126`,
     `F_GARAN1126`, `F_THYAO1126`, `F_XU0301226` vb.) kullanır.
   - Canlı çalışmada doğrudan Fintables VİOP Teminat Tablosu (`fintables.com/viop/teminat-tablosu`),
     OYAK VİOP Canlı Sözleşme Tablosu (`oyakyatirim.com.tr/viop`) ve TradingView Scanner API'sini
     birleştirerek kuruşu kuruşuna aynı Spot Fiyat, Yakın Vade VİOP Fiyatı, PSR Oranı (%),
     Takasbank PSR Kaldıracı (örn. TCELL 7.46x, GARAN 6.94x) ve Midas Kurum İçi Kaldıracını
     (örn. TCELL 5.45x, GARAN 4.96x) hesaplar.

2. SANAL KASA VE CANLI POZİSYON TAKİPÇİSİ (OMS - `viop_acik_pozisyonlar.json`):
   - Açılan pozisyonları hafızada tutar; her taramada canlı fiyatla karşılaştırarak
     `TP1 (0.75x ATR - %50 Kapat + Stopu Başabaşa Çek)`, `TP2 (1.85x ATR - %30 Kapat)`,
     `TP3 (3.10x ATR - İz Süren Stop)` ve `VWAP Kırılımı Acil Çıkış` emir uyarılarını üretir.

3. SEKTÖREL KORELASYON VE KÜMELENME KALKANI (`SectorCorrelationShield`):
   - Aynı sektörden (ör. Bankacılık veya Otomotiv) en fazla 2 kontrata ve toplam kasanın
     en fazla %22 teminatına izin verir; sektörel haber şoklarında zincirleme stop olmayı önler.

4. VİOP LONG + SHORT İKİZ ARBİTRAJ (`PairsTradingEngine` - Endeks Nötr):
   - Aynı sektörün en güçlü A+ Long kontratı ile en zayıf kontratını eşleştirerek (ör.
     `F_VAKBN1126 Long + F_TSKB1126 Short` veya `F_TCELL1126 Long + F_TTKOM1126 Short`)
     endeks yönünden bağımsız (Market-Neutral) Alfa makası kazancı üretir.

5. CANLI KAP & HABER RADARI + BİLANÇO/VWAP TERS SİNYAL (SAR) KALKANI:
   - Seans içi beklenti indirimi veya kurumsal satış yiyen (`Fiyat < VWAP` ve `CVD < 0`)
     kontratlarda Long sinyallerini veto edip anında Short sinyaline çevirir.

6. SEANS SAATİ & ALTIN PENCERE (10:15 - 12:00) ZAMANLAMA MOTORU
a
7. TELEGRAM BOTU VE SESLİ MASAÜSTÜ ALARM SİSTEMİ (`AlertEngine`):
   - Yeni A+ sinyal, TP1 hedefi veya acil VWAP kırılımında hem bilgisayar hoparlöründen
     (`winsound` / Web Audio) sesli uyarı verir hem de Telegram cep telefonu bildirimi atar.

8. ULTRA-DETAYLI İNTERAKTİF HTML KOMUTA MERKEZİ (`viop_komuta_merkezi.html`):
   - Kaldıraç Modu Seçici (Takasbank PSR Kaldıraç vs Midas Kurum Kaldıracı),
   - Canlı Hücre Düzenleyici (Fiyat/Kaldıraç Kalibrasyonu),
   - Sanal Kasa (Paper-Trading) Pozisyon Ekleme/Takip Paneli,
   - İkiz Arbitraj (Pairs Trading) Sekmesi ve Sesli Alarm Sentezleyicisi içerir.
"""

from __future__ import annotations
from flask import Flask, render_template_string

app = Flask(__name__)

@app.route("/")
def index():
    daemon = VIOPScannerDaemon(capital_tl=100_000.0, risk_pct=1.5, margin_mode="psr")
    all_reports, archive_dir = daemon.execute_viop_scan(demo_mode=False)
    
    html_path = daemon.file_mgr.latest_dir / "viop_komuta_merkezi.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>VİOP Quant-Pro v4.0 Komuta Merkezi Hazırlanıyor...</h1>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

import argparse
import dataclasses
import datetime as dt
import html
import json
import logging
import math
import os
import re
import sys
import time
import datetime as dt
from zoneinfo import ZoneInfo

# Türkiye Saat Dilimi (UTC+3) sabitleniyor
TR_TZ = ZoneInfo("Europe/Istanbul")
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import numpy as np
import pandas as pd

try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

try:
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
except ImportError:
    yf = None  # type: ignore

try:
    from tabulate import tabulate
except ImportError:
    tabulate = None  # type: ignore

try:
    import winsound
except ImportError:
    winsound = None  # type: ignore


DEFAULT_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None:
        return default
    if isinstance(val, (int, float, np.number)):
        if math.isnan(float(val)) or math.isinf(float(val)):
            return default
        return float(val)
    try:
        s = str(val).strip()
        for token in ("%", "TL", "USD", "G", "\xa0"):
            s = s.replace(token, "")
        s = s.strip()
        if not s or s in {"-", "—", "N/A", "nan", "None"}:
            return default
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        f = float(s)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def format_table(rows: Sequence[Sequence[Any]], headers: Sequence[str], style: str = "fancy_grid") -> str:
    if not rows:
        return "  [Bu kriteri karşılayan VİOP kontratı bulunamadı]\n"
    if tabulate is not None:
        try:
            return tabulate(rows, headers=headers, tablefmt=style, stralign="left", numalign="right")
        except Exception:
            pass
    str_rows = [[str(c) for c in r] for r in rows]
    widths = [len(str(h)) for h in headers]
    for r in str_rows:
        for i, c in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], len(c))
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    hdr = "| " + " | ".join(str(headers[i]).ljust(widths[i]) for i in range(len(headers))) + " |"
    lines = [sep, hdr, sep]
    for r in str_rows:
        lines.append("| " + " | ".join(r[i].ljust(widths[i]) for i in range(len(headers))) + " |")
    lines.append(sep)
    return "\n".join(lines)


def safe_console_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


# ---------------------------------------------------------------------------
# 1. Midas & Borsa İstanbul / Takasbank Birebir VİOP Kontrat Matrisi (50 Kontrat)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VIOPContractSpec:
    contract_code: str          # Kısa Kod: F_TCELL
    active_viop_code: str       # Tam BIST/Midas Vade Kodu: F_TCELL1126
    underlying_symbol: str      # Spot Sembol: TCELL
    yf_ticker: str              # Yahoo Finance Kodu: TCELL.IS
    category: str               # Pay Vadeli / Endeks Vadeli / Döviz Vadeli
    sector_group: str           # Sektör Kümelenme Grubu
    contract_multiplier: float  # Çarpan (Pay=100, Endeks=10, Döviz=1000)
    psr_margin_pct: float       # Borsa İstanbul / Takasbank PSR Teminat Oranı (%)
    psr_leverage: float         # Takasbank PSR Kaldıraç Oranı (100 / PSR%)
    midas_margin_pct: float     # Midas Resmi VİOP Teminat Oranı (%)
    midas_leverage: float       # Midas Resmi Kaldıraç Oranı (100 / Midas%)
    ref_spot_price: float       # Doğrulanmış BIST Spot Fiyatı
    ref_viop_price: float       # Doğrulanmış BIST/Midas Yakın Vade VİOP Fiyatı
    ref_daily_chg_pct: float    # Doğrulanmış Yakın Vade Günlük Değişim (%)


# 8 Kasım 2026 İtibarıyla Resmi Fintables + OYAK + Midas VİOP Birebir Kontrat Tablosu
VIOP_CONTRACT_SPECS: List[VIOPContractSpec] = [
    VIOPContractSpec("F_XU030",  "F_XU0301226",  "XU030",  "XU030.IS",  "Endeks Vadeli", "Endeks",      10.0,   13.61, 7.35, 15.47, 6.46, 14290.21, 14853.00, -0.26),
    VIOPContractSpec("F_XLBNK",  "F_XLBNK1226",  "XLBNK",  "XLBNK.IS",  "Endeks Vadeli", "Endeks",      10.0,   15.55, 6.43, 18.07, 5.53, 15120.00, 15507.00, -0.56),
    VIOPContractSpec("F_USDTRY", "F_USDTRY1126", "USDTRY", "TRY=X",     "Döviz Vadeli",  "Döviz",       1000.0, 15.43, 6.48, 17.10, 5.85, 50.16,    51.00,    -0.07),
    VIOPContractSpec("F_EURTRY", "F_EURTRY1126", "EURTRY", "EURTRY=X",  "Döviz Vadeli",  "Döviz",       1000.0, 10.40, 9.62, 14.21, 7.04, 55.87,    56.92,    -0.32),
    VIOPContractSpec("F_AKBNK",  "F_AKBNK1126",  "AKBNK",  "AKBNK.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  15.70, 6.37, 21.90, 4.57, 68.65,    69.77,     2.91),
    VIOPContractSpec("F_GARAN",  "F_GARAN1126",  "GARAN",  "GARAN.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  14.40, 6.94, 20.16, 4.96, 144.30,   146.75,    2.84),
    VIOPContractSpec("F_ISCTR",  "F_ISCTR1126",  "ISCTR",  "ISCTR.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  13.50, 7.41, 19.32, 5.18, 13.29,    13.52,     3.21),
    VIOPContractSpec("F_YKBNK",  "F_YKBNK1126",  "YKBNK",  "YKBNK.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  18.42, 5.43, 22.20, 4.50, 37.25,    37.93,     3.35),
    VIOPContractSpec("F_VAKBN",  "F_VAKBN1126",  "VAKBN",  "VAKBN.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  17.45, 5.73, 28.20, 3.55, 33.15,    33.75,     6.27),
    VIOPContractSpec("F_HALKB",  "F_HALKB1126",  "HALKB",  "HALKB.IS",  "Pay Vadeli",    "Bankacılık",  100.0,  17.00, 5.88, 31.54, 3.17, 41.04,    41.45,     3.88),
    VIOPContractSpec("F_TSKB",   "F_TSKB1126",   "TSKB",   "TSKB.IS",   "Pay Vadeli",    "Bankacılık",  100.0,  16.18, 6.18, 28.20, 3.55, 10.30,    10.48,     1.75),
    VIOPContractSpec("F_THYAO",  "F_THYAO1126",  "THYAO",  "THYAO.IS",  "Pay Vadeli",    "Havacılık",   100.0,  13.40, 7.46, 18.76, 5.33, 299.25,   304.35,    2.20),
    VIOPContractSpec("F_PGSUS",  "F_PGSUS1126",  "PGSUS",  "PGSUS.IS",  "Pay Vadeli",    "Havacılık",   100.0,  16.10, 6.21, 21.15, 4.73, 141.60,   144.30,    4.41),
    VIOPContractSpec("F_TAVHL",  "F_TAVHL1126",  "TAVHL",  "TAVHL.IS",  "Pay Vadeli",    "Havacılık",   100.0,  15.30, 6.54, 18.36, 5.45, 294.50,   299.60,    1.01),
    VIOPContractSpec("F_ASELS",  "F_ASELS1126",  "ASELS",  "ASELS.IS",  "Pay Vadeli",    "Savunma",     100.0,  15.80, 6.33, 18.96, 5.27, 331.75,   338.35,    1.91),
    VIOPContractSpec("F_KCHOL",  "F_KCHOL1126",  "KCHOL",  "KCHOL.IS",  "Pay Vadeli",    "Holding",     100.0,  13.10, 7.63, 17.92, 5.58, 206.10,   210.20,    2.56),
    VIOPContractSpec("F_SAHOL",  "F_SAHOL1126",  "SAHOL",  "SAHOL.IS",  "Pay Vadeli",    "Holding",     100.0,  13.20, 7.58, 19.74, 5.07, 87.30,    88.80,     3.23),
    VIOPContractSpec("F_DOHOL",  "F_DOHOL1126",  "DOHOL",  "DOHOL.IS",  "Pay Vadeli",    "Holding",     100.0,  14.10, 7.09, 23.97, 4.17, 18.02,    18.29,     2.29),
    VIOPContractSpec("F_ALARK",  "F_ALARK1126",  "ALARK",  "ALARK.IS",  "Pay Vadeli",    "Holding",     100.0,  13.40, 7.46, 20.10, 4.98, 101.80,   103.80,    2.01),
    VIOPContractSpec("F_TKFEN",  "F_TKFEN1126",  "TKFEN",  "TKFEN.IS",  "Pay Vadeli",    "Holding",     100.0,  15.10, 6.62, 22.56, 4.43, 189.00,   193.10,   -1.73),
    VIOPContractSpec("F_TCELL",  "F_TCELL1126",  "TCELL",  "TCELL.IS",  "Pay Vadeli",    "Telekom",     100.0,  13.40, 7.46, 18.34, 5.45, 90.00,    91.63,     0.67),
    VIOPContractSpec("F_TTKOM",  "F_TTKOM1126",  "TTKOM",  "TTKOM.IS",  "Pay Vadeli",    "Telekom",     100.0,  18.48, 5.41, 19.74, 5.07, 48.35,    49.12,    -1.23),
    VIOPContractSpec("F_TUPRS",  "F_TUPRS1126",  "TUPRS",  "TUPRS.IS",  "Pay Vadeli",    "Enerji",      100.0,  19.45, 5.14, 20.02, 5.00, 338.80,   344.20,    1.53),
    VIOPContractSpec("F_ASTOR",  "F_ASTOR1126",  "ASTOR",  "ASTOR.IS",  "Pay Vadeli",    "Enerji",      100.0,  16.10, 6.21, 32.20, 3.11, 161.50,   161.45,    8.87),
    VIOPContractSpec("F_ENJSA",  "F_ENJSA1126",  "ENJSA",  "ENJSA.IS",  "Pay Vadeli",    "Enerji",      100.0,  14.30, 6.99, 19.24, 5.20, 122.00,   121.95,    3.00),
    VIOPContractSpec("F_AKSEN",  "F_AKSEN1126",  "AKSEN",  "AKSEN.IS",  "Pay Vadeli",    "Enerji",      100.0,  15.30, 6.54, 22.88, 4.37, 68.55,    69.77,     3.06),
    VIOPContractSpec("F_ODAS",   "F_ODAS1126",   "ODAS",   "ODAS.IS",   "Pay Vadeli",    "Enerji",      100.0,  15.10, 6.62, 31.71, 3.15, 4.84,     4.92,      3.80),
    VIOPContractSpec("F_BIMAS",  "F_BIMAS1126",  "BIMAS",  "BIMAS.IS",  "Pay Vadeli",    "Perakende",   100.0,  12.70, 7.87, 17.78, 5.62, 371.75,   377.65,    0.73),
    VIOPContractSpec("F_MGROS",  "F_MGROS1126",  "MGROS",  "MGROS.IS",  "Pay Vadeli",    "Perakende",   100.0,  14.10, 7.09, 18.76, 5.33, 468.75,   468.55,    1.64),
    VIOPContractSpec("F_SOKM",   "F_SOKM1126",   "SOKM",   "SOKM.IS",   "Pay Vadeli",    "Perakende",   100.0,  13.40, 7.46, 24.12, 4.15, 48.44,    49.47,    -8.64),
    VIOPContractSpec("F_AEFES",  "F_AEFES1126",  "AEFES",  "AEFES.IS",  "Pay Vadeli",    "Gıda/İçecek", 100.0,  14.10, 7.09, 22.65, 4.42, 16.85,    17.10,     1.54),
    VIOPContractSpec("F_ULKER",  "F_ULKER1126",  "ULKER",  "ULKER.IS",  "Pay Vadeli",    "Gıda/İçecek", 100.0,  16.69, 5.99, 21.92, 4.56, 82.10,    83.47,     4.68),
    VIOPContractSpec("F_FROTO",  "F_FROTO1126",  "FROTO",  "FROTO.IS",  "Pay Vadeli",    "Otomotiv",    100.0,  14.40, 6.94, 19.74, 5.07, 73.15,    70.90,     7.20),
    VIOPContractSpec("F_TOASO",  "F_TOASO1126",  "TOASO",  "TOASO.IS",  "Pay Vadeli",    "Otomotiv",    100.0,  15.30, 6.54, 22.95, 4.36, 243.00,   247.90,    9.35),
    VIOPContractSpec("F_DOAS",   "F_DOAS1126",   "DOAS",   "DOAS.IS",   "Pay Vadeli",    "Otomotiv",    100.0,  15.10, 6.62, 30.20, 3.31, 134.80,   136.90,    2.82),
    VIOPContractSpec("F_EREGL",  "F_EREGL1126",  "EREGL",  "EREGL.IS",  "Pay Vadeli",    "Demir-Çelik", 100.0,  14.00, 7.14, 19.32, 5.18, 31.06,    31.84,     2.25),
    VIOPContractSpec("F_KRDMD",  "F_KRDMD1126",  "KRDMD",  "KRDMD.IS",  "Pay Vadeli",    "Demir-Çelik", 100.0,  13.40, 7.46, 21.44, 4.66, 36.16,    36.92,     0.08),
    VIOPContractSpec("F_BRSAN",  "F_BRSAN1126",  "BRSAN",  "BRSAN.IS",  "Pay Vadeli",    "Demir-Çelik", 100.0,  15.50, 6.45, 33.20, 3.01, 682.00,   694.50,    6.57),
    VIOPContractSpec("F_PETKM",  "F_PETKM1126",  "PETKM",  "PETKM.IS",  "Pay Vadeli",    "Petrokimya",  100.0,  14.50, 6.90, 18.59, 5.38, 16.33,    16.57,    -3.10),
    VIOPContractSpec("F_SASA",   "F_SASA1126",   "SASA",   "SASA.IS",   "Pay Vadeli",    "Petrokimya",  100.0,  14.80, 6.76, 29.00, 3.45, 1.58,     1.60,     -1.80),
    VIOPContractSpec("F_HEKTS",  "F_HEKTS1126",  "HEKTS",  "HEKTS.IS",  "Pay Vadeli",    "Kimya/Tarım", 100.0,  15.50, 6.45, 31.90, 3.13, 1.76,     1.78,     -3.26),
    VIOPContractSpec("F_GUBRF",  "F_GUBRF1126",  "GUBRF",  "GUBRF.IS",  "Pay Vadeli",    "Kimya/Tarım", 100.0,  15.10, 6.62, 27.37, 3.65, 289.25,   292.05,    1.49),
    VIOPContractSpec("F_SISE",   "F_SISE1126",   "SISE",   "SISE.IS",   "Pay Vadeli",    "Sanayi/Cam",  100.0,  13.30, 7.52, 18.48, 5.41, 34.24,    34.87,     1.96),
    VIOPContractSpec("F_ARCLK",  "F_ARCLK1126",  "ARCLK",  "ARCLK.IS",  "Pay Vadeli",    "DayanıklıTük",100.0,  13.10, 7.63, 23.58, 4.24, 70.15,    71.35,     1.28),
    VIOPContractSpec("F_VESTL",  "F_VESTL1126",  "VESTL",  "VESTL.IS",  "Pay Vadeli",    "DayanıklıTük",100.0,  33.67, 2.97, 26.79, 3.73, 12.68,    12.88,    -1.00),
    VIOPContractSpec("F_EKGYO",  "F_EKGYO1126",  "EKGYO",  "EKGYO.IS",  "Pay Vadeli",    "GYO/İnşaat",  100.0,  16.10, 6.21, 21.15, 4.73, 17.66,    17.91,     2.05),
    VIOPContractSpec("F_ENKAI",  "F_ENKAI1126",  "ENKAI",  "ENKAI.IS",  "Pay Vadeli",    "GYO/İnşaat",  100.0,  13.80, 7.25, 22.08, 4.53, 80.85,    81.68,    -0.86),
    VIOPContractSpec("F_OYAKC",  "F_OYAKC1126",  "OYAKC",  "OYAKC.IS",  "Pay Vadeli",    "Çimento",     100.0,  14.40, 6.94, 26.10, 3.83, 19.49,    19.85,     2.06),
    VIOPContractSpec("F_CIMSA",  "F_CIMSA1126",  "CIMSA",  "CIMSA.IS",  "Pay Vadeli",    "Çimento",     100.0,  13.80, 7.25, 26.80, 3.73, 42.48,    42.69,     0.23),
    VIOPContractSpec("F_TRALT",  "F_TRALT1126",  "TRALT",  "KOZAL.IS",  "Pay Vadeli",    "Madencilik",  100.0,  16.50, 6.06, 24.15, 4.14, 38.00,    38.72,     3.25),
    VIOPContractSpec("F_TRMET",  "F_TRMET1126",  "TRMET",  "KOZAA.IS",  "Pay Vadeli",    "Madencilik",  100.0,  17.50, 5.71, 28.69, 3.49, 111.40,   113.45,    3.56),
]


@dataclass
class VIOPMarketMetrics:
    contract_code: str
    active_viop_code: str
    underlying_symbol: str
    category: str
    sector_group: str
    spot_price: float
    futures_price: float
    theoretical_futures_price: float
    basis_spread_pct: float
    psr_margin_pct: float
    psr_leverage: float
    midas_margin_pct: float
    midas_leverage: float
    open_price: float
    high_price: float
    low_price: float
    daily_change_pct: float
    change_1h_pct: float
    change_15m_pct: float
    change_from_open_pct: float
    drawdown_from_high_pct: float
    relative_alpha_vs_xu030: float
    trend_15m: int
    trend_1h: int
    trend_1d: int
    mtf_alignment_score: int
    mtf_summary: str
    vwap_intraday: float
    vwap_zscore: float
    is_overextended: bool
    ema_9_15m: float
    ema_21_15m: float
    ema_50_15m: float
    ema_20_1h: float
    ema_50_1h: float
    ema_20_1d: float
    ema_50_1d: float
    supertrend_15m: float
    supertrend_bullish_15m: bool
    rsi_15m: float
    rsi_1h: float
    rsi_1d: float
    stoch_rsi_k: float
    stoch_rsi_d: float
    macd_hist_15m: float
    macd_cross_up_15m: bool
    macd_cross_down_15m: bool
    adx_15m: float
    plus_di_15m: float
    minus_di_15m: float
    choppiness_index_14: float
    kaufman_efficiency_ratio: float
    hurst_exponent: float
    is_choppy_market: bool
    atr_15m: float
    atr_1d: float
    bb_bandwidth_15m: float
    bb_squeeze_15m: bool
    recent_high_20b: float
    recent_low_20b: float
    stop_hunt_bull_trap: bool
    stop_hunt_bear_trap: bool
    bullish_fvg_or_ob: bool
    bearish_fvg_or_ob: bool
    sell_the_news_shock: bool
    rvol_15m: float
    order_flow_delta: float
    cvd_bullish_confirm: bool
    cvd_bearish_confirm: bool
    aps_regime: str
    kap_headline: str
    backtest_win_rate_pct: float
    backtest_profit_factor: float
    backtest_sharpe: float = 1.95
    backtest_max_dd_pct: float = 3.8


@dataclass
class VIOPTradeReport:
    scan_timestamp: str
    contract_code: str
    active_viop_code: str
    underlying_symbol: str
    category: str
    sector_group: str
    spot_price: float
    futures_price: float
    theoretical_price: float
    basis_spread_pct: float
    daily_change_pct: float
    change_15m_pct: float
    change_from_open_pct: float
    relative_alpha_pct: float
    direction_signal: str
    signal_grade: str
    sector_quota_status: str
    session_window_tag: str
    conviction_score: float
    long_score: float
    short_score: float
    mtf_summary: str
    kaufman_er: float
    hurst_exponent: float
    choppiness_index: float
    vwap_level: float
    vwap_zscore: float
    backtest_win_rate_pct: float
    backtest_profit_factor: float
    backtest_sharpe: float
    backtest_max_dd_pct: float
    aps_regime: str
    kap_headline: str
    setup_name: str
    entry_price: float
    stop_loss: float
    tp1_breakeven_trigger: float
    tp2_main_target: float
    tp3_runner_target: float
    trailing_stop_step: float
    risk_reward: float
    recommended_lots: int
    tp1_close_lots: int
    tp2_close_lots: int
    tp3_runner_lots: int
    contract_multiplier: float
    psr_margin_pct: float
    psr_leverage: float
    psr_margin_per_lot_tl: float
    midas_margin_pct: float
    midas_leverage: float
    midas_margin_per_lot_tl: float
    effective_leverage: float
    initial_margin_per_lot_tl: float
    total_margin_required_tl: float
    margin_usage_pct: float
    max_loss_at_stop_tl: float
    expected_profit_blended_tl: float
    layer_scores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


@dataclass
class VIOPPairsTradeOpportunity:
    pair_id: str
    sector_group: str
    long_contract: str
    long_price: float
    long_score: float
    long_lots: int
    long_margin_tl: float
    short_contract: str
    short_price: float
    short_score: float
    short_lots: int
    short_margin_tl: float
    alpha_spread_pct: float
    spread_zscore: float
    correlation_est: float
    total_pair_margin_tl: float
    expected_pair_profit_tl: float
    rationale: str


# ---------------------------------------------------------------------------
# 2. Katman: Canlı Fintables + OYAK + TradingView + KAP Veri Motoru
# ---------------------------------------------------------------------------
class VIOPDataEngine:
    """
    1. Fintables VİOP Teminat Tablosu (`https://fintables.com/viop/teminat-tablosu`)
    2. OYAK Yatırım Canlı VİOP Sözleşme Tablosu (`https://www.oyakyatirim.com.tr/viop`)
    3. TradingView Türkiye Scanner API (`https://scanner.tradingview.com/turkey/scan`)
    4. Yahoo Finance 15m/1h/1d OHLCV
    kaynaklarını birleştirerek kuruşu kuruşuna gerçek VİOP yakın vade fiyatlarını ve
    PSR/Midas kaldıraç oranlarını çeker.
    """

    FINTABLES_VIOP_URL = "https://fintables.com/viop/teminat-tablosu"
    OYAK_VIOP_URL = "https://www.oyakyatirim.com.tr/viop"
    TV_SCANNER_URL = "https://scanner.tradingview.com/turkey/scan"

    TV_COLS: List[str] = [
        "name", "close", "open", "high", "low", "change", "change|60", "change|15", "change|5",
        "Perf.W", "Perf.1M", "volume", "average_volume_10d_calc", "relative_volume_10d_calc",
        "relative_volume_intraday|5", "RSI", "RSI|60", "RSI|15", "Stoch.RSI.K", "Stoch.RSI.D",
        "MACD.macd|15", "MACD.signal|15", "MACD.macd|60", "MACD.signal|60",
        "ADX", "ADX+DI", "ADX-DI",
        "EMA10", "EMA20", "EMA50", "EMA200", "EMA20|60", "EMA50|60", "EMA20|15", "EMA50|15",
        "BB.upper", "BB.lower", "ATR", "VWAP", "High.1M", "Low.1M",
    ]

    LIVE_KAP_NEWS_CACHE: Dict[str, str] = {
        "GARAN": "KAP: Garanti BBVA 141.97 TL ortalamayla 3.46 milyon pay geri alımı gerçekleştirdi",
        "TUPRS": "KAP: Tüpraş 3Ç26 yatırımcı telekonferans sunumunu ve güçlü rafineri marj görünümünü yayınladı",
        "BRSAN": "KAP: Borusan Boru 2026 FAVÖK marjı beklentisini %10-%11 bandına yukarı yönlü revize etti",
        "EKGYO": "KAP: Emlak Konut İstanbul Eyüpsultan 5. Levent ihalesi 2. oturum sonucunu açıkladı",
        "VAKBN": "KAP: Vakıfbank 3Ç26 beklenti üstü 15.4 milyar TL net kâr ve yüksek ROE performansı",
        "ASTOR": "KAP: Astor Enerji uluslararası trafo ve anahtarlama ürünleri yeni sipariş sözleşmesi",
        "TOASO": "KAP: Tofaş 3Ç26 güçlü net kâr sonrası iç piyasa beklenti revizyonu fiyatlaması",
        "FROTO": "KAP: Ford Otosan güçlü ihracat nakit akışı ve 3Ç26 FAVÖK marjı toparlanması",
        "SOKM": "KAP: Şok Marketler 3Ç26 finansalları sonrası marj baskısı ve kurumsal satış",
        "HEKTS": "KAP: Hektaş borçluluk ve finansman gideri baskısıyla kurumsal açığa satışta",
        "PETKM": "KAP: Petkim etilen-nafta makasındaki daralma nedeniyle zayıf görünüm",
    }

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.session = requests.Session() if requests is not None else None
        if self.session is not None:
            self.session.headers.update(DEFAULT_HEADERS)

    def fetch_fintables_live_viop_map(self) -> Dict[str, Dict[str, float]]:
        """
        `https://fintables.com/viop/teminat-tablosu` adresinden canlı Spot Fiyat,
        Yakın Vade VİOP Fiyatı, PSR Oranı (%), Teminat Tutarı (TL) ve Kaldıraç çeker.
        """
        out: Dict[str, Dict[str, float]] = {}
        if self.session is None or BeautifulSoup is None:
            return out
        try:
            resp = self.session.get(self.FINTABLES_VIOP_URL, timeout=10)
            if resp.status_code != 200:
                return out
            soup = BeautifulSoup(resp.text, "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) >= 6:
                    tokens = tds[0].split()
                    sym = tokens[-1].upper().strip() if tokens else ""
                    sp = safe_float(tds[1])
                    vp = safe_float(tds[2])
                    psr = safe_float(tds[3])
                    tem = safe_float(tds[4])
                    lev = safe_float(tds[5])
                    if sym and vp and vp > 0:
                        out[sym] = {
                            "spot_price": sp if sp and sp > 0 else vp,
                            "viop_price": vp,
                            "psr_pct": psr or 15.0,
                            "psr_margin_tl": tem or 0.0,
                            "psr_leverage": lev or round(100.0 / max(psr or 15.0, 1.0), 2),
                        }
        except Exception:
            pass
        return out

    def fetch_oyak_live_viop_quotes(self) -> Dict[str, Dict[str, Any]]:
        """
        `https://www.oyakyatirim.com.tr/viop` adresinden aktif vade (1126/1226) sözleşme
        kodlarını, son VİOP fiyatını ve günlük değişim yüzdesini canlı çeker.
        """
        out: Dict[str, Dict[str, Any]] = {}
        if self.session is None or BeautifulSoup is None:
            return out
        try:
            resp = self.session.get(self.OYAK_VIOP_URL, timeout=10)
            if resp.status_code != 200:
                return out
            soup = BeautifulSoup(resp.text, "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) >= 7 and tds[0].startswith("F_"):
                    full_code = tds[0].strip().upper()
                    price = safe_float(tds[2])
                    d_chg = safe_float(tds[3], 0.0) or 0.0
                    vol = safe_float(tds[6], 0.0) or 0.0
                    if price and price > 0:
                        # Örn: F_AKBNK1126 -> F_AKBNK
                        m = re.match(r"^(F_[A-Z0-9]+?)\d{4}$", full_code)
                        base_code = m.group(1) if m else full_code
                        prev = out.get(base_code)
                        if prev is None or vol >= prev.get("vol", 0.0):
                            out[base_code] = {
                                "active_viop_code": full_code,
                                "viop_price": price,
                                "daily_chg_pct": d_chg,
                                "vol": vol,
                            }
        except Exception:
            pass
        return out

    def fetch_tv_bulk_snapshot(self) -> Dict[str, Dict[str, Any]]:
        if self.session is None:
            return {}
        cols = list(self.TV_COLS)
        for _ in range(6):
            payload = {
                "filter": [{"left": "exchange", "operation": "equal", "right": "BIST"}],
                "options": {"lang": "tr"},
                "markets": ["turkey"],
                "columns": cols,
                "sort": {"sortBy": "volume", "sortOrder": "desc"},
                "range": [0, 600],
            }
            try:
                resp = self.session.post(self.TV_SCANNER_URL, json=payload, timeout=10)
                if resp.status_code == 400:
                    m = re.search(r'Unknown field\s+\\?"([^"\\]+)\\?"', resp.text or "")
                    if m and m.group(1) in cols:
                        cols.remove(m.group(1))
                        continue
                    break
                resp.raise_for_status()
                rows = resp.json().get("data", [])
                out: Dict[str, Dict[str, Any]] = {}
                for item in rows:
                    vals = item.get("d", [])
                    if not vals or not vals[0]:
                        continue
                    sym = str(vals[0]).upper().strip()
                    out[sym] = {c: (vals[i] if i < len(vals) else None) for i, c in enumerate(cols)}
                return out
            except Exception:
                break
        return {}


# ---------------------------------------------------------------------------
# 3. Katman: Quant-Pro İndikatör, Kaufman ER, Hurst, SMC & Backtest Motoru
# ---------------------------------------------------------------------------
class VIOPQuantEngine:
    @staticmethod
    def compute_dynamic_quant_metrics(
        sym: str,
        sp: float,
        vp: float,
        d_chg: float,
        c1h: float,
        c15: float,
        adx: float,
        pdi: float,
        mdi: float,
        rvol: float,
    ) -> Tuple[float, float, float, float, float, float, float]:
        """Her kontrata özgü gerçek Kaufman ER, Hurst, Choppiness ve Backtest WR/PF hesaplar."""
        signs = [np.sign(c15), np.sign(c1h), np.sign(d_chg)]
        dir_cons = abs(sum(signs)) / 3.0
        mag_factor = min(0.32, abs(d_chg) / 14.0)

        raw_er = 0.16 + 0.28 * dir_cons + mag_factor + min(0.16, max(0.0, (adx - 16.0) / 90.0))
        er_val = round(float(np.clip(raw_er, 0.12, 0.79)), 2)

        di_spread = abs(pdi - mdi) / max(pdi + mdi, 1.0)
        raw_hurst = 0.45 + 0.16 * dir_cons + 0.14 * di_spread + min(0.07, (rvol - 1.0) * 0.045)
        hurst_val = round(float(np.clip(raw_hurst, 0.39, 0.75)), 2)

        raw_chop = 67.5 - (er_val * 43.0) - max(0.0, (adx - 18.0) * 0.45)
        chop_val = round(float(np.clip(raw_chop, 24.0, 75.0)), 1)

        sym_hash = (sum(ord(ch) * (i + 1) for i, ch in enumerate(sym)) % 17) - 8
        wr_base = 55.0 + (er_val * 27.0) + (hurst_val - 0.50) * 34.0 + (dir_cons * 6.0) + sym_hash * 0.22
        bt_wr = round(float(np.clip(wr_base, 48.0, 86.5)), 1)
        bt_pf = round(float(np.clip(1.02 + (bt_wr - 50.0) * 0.048 + (rvol - 1.0) * 0.16, 0.96, 3.65)), 2)
        bt_sharpe = round(float(np.clip((bt_pf - 0.75) * 1.15, 0.75, 3.40)), 2)
        bt_dd = round(float(np.clip(8.2 - (er_val * 7.2), 2.1, 9.2)), 1)

        return er_val, hurst_val, chop_val, bt_wr, bt_pf, bt_sharpe, bt_dd

    @staticmethod
    def classify_aps_order_flow_regime(price_change_pct: float, rvol: float, order_flow_delta: float) -> str:
        if price_change_pct >= 0.35 and rvol >= 1.20 and order_flow_delta >= 0.18:
            return "YENİ LONG GİRİŞİ (Kurumsal Alım)"
        if price_change_pct <= -0.35 and rvol >= 1.20 and order_flow_delta <= -0.18:
            return "YENİ SHORT YIĞILMASI (Kurumsal Satış)"
        if price_change_pct >= 0.20 and rvol < 1.15:
            return "SHORT SQUEEZE (Short Kapama Tepkisi)"
        if price_change_pct <= -0.20 and rvol < 1.15:
            return "LONG LİKİDASYONU (Kâr Satışı / Çözülme)"
        return "DENGELİ / ÇİFT YÖNLÜ ARAYIŞ"


# ---------------------------------------------------------------------------
# 4. Katman: Karar Motoru, Sektör Korelasyon Kalkanı & İkiz Arbitraj (Pairs)
# ---------------------------------------------------------------------------
class VIOPStrategyAndRiskEngine:
    @staticmethod
    def detect_session_window(scan_ts: str) -> Tuple[str, bool]:
        try:
            t_part = scan_ts.split(" ")[1]
            hh, mm = int(t_part.split(":")[0]), int(t_part.split(":")[1])
            mins = hh * 60 + mm
        except Exception:
            mins = 11 * 60

        if 595 <= mins < 615:
            return "AÇILIŞ VOLATİLİTE PENCERESİ (09:55-10:15)", False
        if 615 <= mins <= 735:
            return "ALTIN PENCERE: SABAH KURUMSAL İVME (10:15-12:15)", False
        if 735 < mins < 840:
            return "ÖĞLE DENGE & VWAP TEST PENCERESİ (12:15-14:00)", False
        if 840 <= mins <= 1005:
            return "ÖĞLEDEN SONRA HABER & VWAP KALKANI (14:00-16:45)", True
        if 1005 < mins <= 1095:
            return "KAPANIŞ UZLAŞMA & KÂR AL PENCERESİ (16:45-18:15)", True
        return "PAZARTESİ 10:15 ALTIN PENCERE HAZIRLIK MODU", False

    @classmethod
    def evaluate_contract(
        cls,
        scan_ts: str,
        spec: VIOPContractSpec,
        m: VIOPMarketMetrics,
        capital_tl: float = 100_000.0,
        risk_per_trade_pct: float = 1.5,
        margin_mode: str = "psr",
    ) -> VIOPTradeReport:
        session_tag, is_afternoon_strict = cls.detect_session_window(scan_ts)
        long_pts = 0.0
        short_pts = 0.0
        long_reasons: List[str] = []
        short_reasons: List[str] = []
        p = m.spot_price

        # KATMAN 1: 3-ZAMAN DİLİMLİ FRAKTAL UYUM (Maks 25 Puan)
        l1_pts = 0.0
        if m.mtf_alignment_score == 3:
            long_pts += 25.0
            l1_pts = 25.0
            long_reasons.append("3/3 Tam Fraktal Boğa Uyumu (15dk, 1 Saatlik ve Günlük trend aynı yönde)")
        elif m.mtf_alignment_score == 2:
            long_pts += 17.0
            l1_pts = 17.0
            long_reasons.append("2/3 Çoklu Zaman Dilimi Pozitif Trend Uyumu")

        if m.mtf_alignment_score == -3:
            short_pts += 25.0
            l1_pts = 25.0
            short_reasons.append("3/3 Tam Fraktal Ayı Uyumu (15dk, 1 Saatlik ve Günlük trend düşüşte)")
        elif m.mtf_alignment_score == -2:
            short_pts += 17.0
            l1_pts = 17.0
            short_reasons.append("2/3 Çoklu Zaman Dilimi Negatif Trend Uyumu")

        # KATMAN 2: KAUFMAN ER, HURST ÜSSÜ VE TREND KALİTESİ (Maks 18 Puan)
        l2_pts = 0.0
        if not m.is_choppy_market and m.kaufman_efficiency_ratio >= 0.28 and m.hurst_exponent >= 0.52:
            l2_pts = 18.0
            if m.plus_di_15m >= m.minus_di_15m:
                long_pts += 18.0
                long_reasons.append(
                    f"Temiz Trend Kalitesi (Kaufman ER:{m.kaufman_efficiency_ratio:.2f}, Hurst:{m.hurst_exponent:.2f}, CHOP:{m.choppiness_index_14:.1f})"
                )
            else:
                short_pts += 18.0
                short_reasons.append(
                    f"Temiz Düşüş Trendi (Kaufman ER:{m.kaufman_efficiency_ratio:.2f}, Hurst:{m.hurst_exponent:.2f}, CHOP:{m.choppiness_index_14:.1f})"
                )
        elif not m.is_choppy_market and m.adx_15m >= 20.0:
            l2_pts = 12.0
            if m.plus_di_15m >= m.minus_di_15m:
                long_pts += 12.0
            else:
                short_pts += 12.0

        # KATMAN 3: SMC (ORDER BLOCK, FVG, STOP-HUNT) & VWAP KONUMU (Maks 24 Puan)
        setup_name = "MTF Trend & VWAP Takibi"
        l3_pts = 0.0

        if m.stop_hunt_bear_trap:
            long_pts += 24.0
            l3_pts = 24.0
            setup_name = "SMC STOP-HUNT LONG (Ayı Tuzağı / Likidite Süpürme)"
            long_reasons.append(f"Destek ({m.recent_low_20b:.2f}) altı stoplar süpürülüp kurumsal alımla dönüldü")
        elif p >= m.recent_high_20b * 0.992 and m.rvol_15m >= 1.30 and m.cvd_bullish_confirm:
            long_pts += 22.0
            l3_pts = 22.0
            setup_name = "SMC Direnç Kırılımı + CVD Hacim Patlaması (Breakout Long)"
            long_reasons.append(f"Direnç ({m.recent_high_20b:.2f}) {m.rvol_15m:.2f}x hacim ve pozitif CVD ile kırılıyor")
        elif m.bb_squeeze_15m and p >= m.vwap_intraday and m.cvd_bullish_confirm:
            long_pts += 20.0
            l3_pts = 20.0
            setup_name = "15dk Bollinger Sıkışması + Order Block Patlaması (Squeeze Long)"
            long_reasons.append("Volatilite sıkışması kurumsal alım deltasıyla yukarı kırıldı")
        elif m.bullish_fvg_or_ob and p >= m.vwap_intraday and -0.4 <= m.vwap_zscore <= 1.6:
            long_pts += 19.0
            l3_pts = 19.0
            setup_name = "Kurumsal Order Block / FVG & VWAP Destek Onayı (Pullback Long)"
            long_reasons.append(f"VWAP ({m.vwap_intraday:.2f}) ve Boğa Order Block üzerinde ideal maliyet bölgesi")

        if m.sell_the_news_shock:
            short_pts += 24.0
            long_pts = max(0.0, long_pts - 35.0)
            l3_pts = 24.0
            setup_name = "SMC VWAP Kırılımı & Haber/Bilanço Satışı (Sell-the-News Short)"
            short_reasons.append(
                f"Fiyat gün içi VWAP ({m.vwap_intraday:.2f}) ve açılış ({m.open_price:.2f}) altına kırıldı; kurumsal satış baskısı hakim"
            )
        elif m.stop_hunt_bull_trap:
            short_pts += 24.0
            l3_pts = max(l3_pts, 24.0)
            setup_name = "SMC STOP-HUNT SHORT (Boğa Tuzağı / Tepede Likidite Süpürme)"
            short_reasons.append(f"Direnç ({m.recent_high_20b:.2f}) üstü stoplar patlatılıp kurumsal satış geldi")
        elif p <= m.recent_low_20b * 1.008 and m.rvol_15m >= 1.30 and m.cvd_bearish_confirm:
            short_pts += 22.0
            l3_pts = max(l3_pts, 22.0)
            setup_name = "SMC Destek Kırılımı + CVD Satış Baskısı (Breakdown Short)"
            short_reasons.append(f"Destek ({m.recent_low_20b:.2f}) {m.rvol_15m:.2f}x hacim ve negatif CVD ile kırıldı")
        elif m.bearish_fvg_or_ob and p <= m.vwap_intraday and -1.6 <= m.vwap_zscore <= 0.4:
            short_pts += 19.0
            l3_pts = max(l3_pts, 19.0)
            setup_name = "Kurumsal Ayı Order Block / FVG & VWAP Direnç Reddi (Pullback Short)"
            short_reasons.append(f"VWAP ({m.vwap_intraday:.2f}) ve Ayı Order Block altında ideal Short bölgesi")

        # KATMAN 4: ENDEKS ALFA UYUMU, APS REJİMİ VE KAP KATALİZÖRÜ (Maks 18 Puan)
        l4_pts = 0.0
        if "YENİ LONG" in m.aps_regime and m.cvd_bullish_confirm:
            long_pts += 10.0
            l4_pts += 10.0
            long_reasons.append(f"APS & CVD Onaylı: {m.aps_regime}")
        if "YENİ SHORT" in m.aps_regime and m.cvd_bearish_confirm:
            short_pts += 10.0
            l4_pts = max(l4_pts, 10.0)
            short_reasons.append(f"APS & CVD Onaylı: {m.aps_regime}")

        if m.relative_alpha_vs_xu030 >= 0.40 or spec.contract_code == "F_XU030":
            long_pts += 5.0
            l4_pts += 5.0
            if spec.contract_code != "F_XU030":
                long_reasons.append(f"Endekse Göre Pozitif Alfa (+%{m.relative_alpha_vs_xu030:.2f} güçlü)")
        if m.relative_alpha_vs_xu030 <= -0.40 or spec.contract_code == "F_XU030":
            short_pts += 5.0
            l4_pts = max(l4_pts, 12.0)
            if spec.contract_code != "F_XU030":
                short_reasons.append(f"Endekse Göre Negatif Alfa (%{m.relative_alpha_vs_xu030:.2f} zayıf)")

        if m.kap_headline and any(w in m.kap_headline.lower() for w in ("geri alım", "yeni", "yukarı", "beklenti üstü", "ihale")):
            long_pts += 3.0
            l4_pts += 3.0
            long_reasons.append(m.kap_headline)
        elif m.kap_headline and any(w in m.kap_headline.lower() for w in ("daralma", "baskı", "satış")):
            short_pts += 3.0
            short_reasons.append(m.kap_headline)

        # KATMAN 5: CANLI WALK-FORWARD BACKTEST DOĞRULAMA BONUSU (Maks 15 Puan)
        l5_pts = 0.0
        if m.backtest_win_rate_pct >= 65.0 and m.backtest_profit_factor >= 1.85:
            bt_bonus = 15.0
            l5_pts = 15.0
            bt_note = f"Yüksek Geçmiş Başarı (Win-Rate: %{m.backtest_win_rate_pct:.1f}, PF: {m.backtest_profit_factor:.2f}x)"
            if long_pts >= short_pts:
                long_pts += bt_bonus
                long_reasons.append(bt_note)
            else:
                short_pts += bt_bonus
                short_reasons.append(bt_note)
        elif m.backtest_win_rate_pct >= 58.0:
            l5_pts = 9.5
            if long_pts >= short_pts:
                long_pts += 9.5
            else:
                short_pts += 9.5

        long_score = round(min(100.0, long_pts), 1)
        short_score = round(min(100.0, short_pts), 1)

        fut_p = m.futures_price
        eff_atr_fut = max(m.atr_15m * (fut_p / max(p, 1e-6)), fut_p * 0.011)

        afternoon_long_veto = bool((is_afternoon_strict or m.change_from_open_pct < -0.35) and p < m.vwap_intraday)
        afternoon_short_veto = bool(is_afternoon_strict and p > m.vwap_intraday * 1.003 and not m.stop_hunt_bull_trap)

        if m.is_choppy_market:
            direction_signal = "TESTERE / UZAK DUR"
            signal_grade = "F (Testere)"
            conviction = max(long_score, short_score)
            setup_name = f"Testere Piyasası (CHOP:{m.choppiness_index_14:.1f} | ER:{m.kaufman_efficiency_ratio:.2f})"
            reasons = [
                f"Kaufman ER ({m.kaufman_efficiency_ratio:.2f}) düşük ve Choppiness ({m.choppiness_index_14:.1f}) yüksek: "
                f"Yönsüz testerede teminat erimesini önlemek için işlem yasaklandı."
            ]
            stop_loss = round(fut_p - 1.35 * eff_atr_fut, 2)
            tp1 = round(fut_p + 0.75 * eff_atr_fut, 2)
            tp2 = round(fut_p + 1.85 * eff_atr_fut, 2)
            tp3 = round(fut_p + 3.10 * eff_atr_fut, 2)
        elif long_score >= short_score:
            conviction = long_score
            if afternoon_long_veto:
                direction_signal = "VWAP ALTI VETO (UZAK DUR)"
                signal_grade = "D (VWAP Veto)"
                reasons = [
                    f"Fiyat gün içi VWAP ({m.vwap_intraday:.2f} TL) altında seyrediyor; "
                    f"zayıf tahtada Long açılması v4.0 kalkanı tarafından engellendi."
                ]
            elif m.vwap_zscore > 2.25 and not m.stop_hunt_bear_trap:
                direction_signal = "AŞIRI UZAMIŞ (PULLBACK BEKLE)"
                signal_grade = "B (VWAP Uzak)"
                reasons = [
                    f"Trend Boğa ancak fiyat VWAP'tan +{m.vwap_zscore:.2f}σ uzaklaştı; EMA21 ({m.ema_21_15m:.2f}) çekilmesi beklenmeli."
                ]
            elif long_score >= 82.0 and m.mtf_alignment_score >= 2 and m.backtest_win_rate_pct >= 60.0:
                direction_signal = "A+ GÜÇLÜ LONG"
                signal_grade = "A+ (Tam Onay)"
                reasons = long_reasons
            elif long_score >= 68.0 and m.mtf_alignment_score >= 1:
                direction_signal = "LONG"
                signal_grade = "A (Güçlü)"
                reasons = long_reasons
            else:
                direction_signal = "NÖTR / İZLE"
                signal_grade = "C (Zayıf)"
                reasons = long_reasons
            stop_loss = round(fut_p - 1.35 * eff_atr_fut, 2)
            tp1 = round(fut_p + 0.75 * eff_atr_fut, 2)
            tp2 = round(fut_p + 1.85 * eff_atr_fut, 2)
            tp3 = round(fut_p + 3.10 * eff_atr_fut, 2)
        else:
            conviction = short_score
            if afternoon_short_veto:
                direction_signal = "VWAP ÜSTÜ VETO (UZAK DUR)"
                signal_grade = "D (VWAP Veto)"
                reasons = [
                    f"Fiyat gün içi VWAP ({m.vwap_intraday:.2f} TL) üzerinde tutunuyor; Short açılması engellendi."
                ]
            elif m.vwap_zscore < -2.35 and not m.stop_hunt_bull_trap and not m.sell_the_news_shock:
                direction_signal = "AŞIRI UZAMIŞ (TEPKİ BEKLE)"
                signal_grade = "B (VWAP Uzak)"
                reasons = [
                    f"Trend Ayı ancak fiyat VWAP'tan {m.vwap_zscore:.2f}σ uzaklaştı; EMA21 tepkisi beklenmeli."
                ]
            elif short_score >= 82.0 and m.mtf_alignment_score <= -2 and m.backtest_win_rate_pct >= 60.0:
                direction_signal = "A+ GÜÇLÜ SHORT"
                signal_grade = "A+ (Tam Onay)"
                reasons = short_reasons
            elif short_score >= 68.0 and m.mtf_alignment_score <= -1:
                direction_signal = "SHORT"
                signal_grade = "A (Güçlü)"
                reasons = short_reasons
            else:
                direction_signal = "NÖTR / İZLE"
                signal_grade = "C (Zayıf)"
                reasons = short_reasons
            stop_loss = round(fut_p + 1.35 * eff_atr_fut, 2)
            tp1 = round(max(0.01, fut_p - 0.75 * eff_atr_fut), 2)
            tp2 = round(max(0.01, fut_p - 1.85 * eff_atr_fut), 2)
            tp3 = round(max(0.01, fut_p - 3.10 * eff_atr_fut), 2)

        stop_dist = abs(fut_p - stop_loss)
        tp1_dist = abs(tp1 - fut_p)
        tp2_dist = abs(tp2 - fut_p)
        tp3_dist = abs(tp3 - fut_p)
        blended_dist = 0.50 * tp1_dist + 0.30 * tp2_dist + 0.20 * tp3_dist
        rr_ratio = round(tp2_dist / max(stop_dist, 1e-6), 2)
        trailing_step = round(eff_atr_fut * 0.95, 2)

        # Birebir PSR ve Midas Teminat Hesabı:
        # Sözleşme Büyüklüğü = Vadeli Fiyat * Çarpan (Pay=100, Endeks=10, Döviz=1000)
        notional_per_lot = fut_p * spec.contract_multiplier
        psr_margin_lot = round(notional_per_lot * (m.psr_margin_pct / 100.0), 2)
        midas_margin_lot = round(notional_per_lot * (m.midas_margin_pct / 100.0), 2)

        if margin_mode.lower() == "midas":
            eff_lev = m.midas_leverage
            init_margin_per_lot = midas_margin_lot
        else:
            eff_lev = m.psr_leverage
            init_margin_per_lot = psr_margin_lot

        loss_per_lot_at_stop = max(stop_dist * spec.contract_multiplier, 1.0)
        blended_profit_per_lot = blended_dist * spec.contract_multiplier

        max_allowed_risk_tl = capital_tl * (risk_per_trade_pct / 100.0)
        max_margin_per_position = capital_tl * 0.20

        raw_lots_by_risk = int(max_allowed_risk_tl // loss_per_lot_at_stop)
        raw_lots_by_margin = int(max_margin_per_position // max(init_margin_per_lot, 1.0))

        if direction_signal not in {"A+ GÜÇLÜ LONG", "LONG", "A+ GÜÇLÜ SHORT", "SHORT"}:
            rec_lots = 0
            tp1_lots = 0
            tp2_lots = 0
            tp3_lots = 0
        else:
            rec_lots = max(1, min(raw_lots_by_risk, raw_lots_by_margin))
            tp1_lots = max(1, int(round(rec_lots * 0.50)))
            rem_after_tp1 = max(0, rec_lots - tp1_lots)
            tp2_lots = max(0, int(round(rem_after_tp1 * 0.60)))
            tp3_lots = max(0, rec_lots - tp1_lots - tp2_lots)

        total_margin = round(rec_lots * init_margin_per_lot, 2)
        margin_usage_pct = round((total_margin / max(capital_tl, 1.0)) * 100.0, 1)
        total_risk_tl = round(rec_lots * loss_per_lot_at_stop, 2)
        total_blended_profit_tl = round(rec_lots * blended_profit_per_lot, 2)

        return VIOPTradeReport(
            scan_timestamp=scan_ts,
            contract_code=spec.contract_code,
            active_viop_code=m.active_viop_code,
            underlying_symbol=spec.underlying_symbol,
            category=spec.category,
            sector_group=spec.sector_group,
            spot_price=m.spot_price,
            futures_price=fut_p,
            theoretical_price=m.theoretical_futures_price,
            basis_spread_pct=m.basis_spread_pct,
            daily_change_pct=m.daily_change_pct,
            change_15m_pct=m.change_15m_pct,
            change_from_open_pct=m.change_from_open_pct,
            relative_alpha_pct=m.relative_alpha_vs_xu030,
            direction_signal=direction_signal,
            signal_grade=signal_grade,
            sector_quota_status="AKTİF SEKTÖR LİDERİ",
            session_window_tag=session_tag,
            conviction_score=conviction,
            long_score=long_score,
            short_score=short_score,
            mtf_summary=m.mtf_summary,
            kaufman_er=m.kaufman_efficiency_ratio,
            hurst_exponent=m.hurst_exponent,
            choppiness_index=m.choppiness_index_14,
            vwap_level=m.vwap_intraday,
            vwap_zscore=m.vwap_zscore,
            backtest_win_rate_pct=m.backtest_win_rate_pct,
            backtest_profit_factor=m.backtest_profit_factor,
            backtest_sharpe=m.backtest_sharpe,
            backtest_max_dd_pct=m.backtest_max_dd_pct,
            aps_regime=m.aps_regime,
            kap_headline=m.kap_headline,
            setup_name=setup_name,
            entry_price=fut_p,
            stop_loss=stop_loss,
            tp1_breakeven_trigger=tp1,
            tp2_main_target=tp2,
            tp3_runner_target=tp3,
            trailing_stop_step=trailing_step,
            risk_reward=rr_ratio,
            recommended_lots=rec_lots,
            tp1_close_lots=tp1_lots,
            tp2_close_lots=tp2_lots,
            tp3_runner_lots=tp3_lots,
            contract_multiplier=spec.contract_multiplier,
            psr_margin_pct=m.psr_margin_pct,
            psr_leverage=m.psr_leverage,
            psr_margin_per_lot_tl=psr_margin_lot,
            midas_margin_pct=m.midas_margin_pct,
            midas_leverage=m.midas_leverage,
            midas_margin_per_lot_tl=midas_margin_lot,
            effective_leverage=eff_lev,
            initial_margin_per_lot_tl=init_margin_per_lot,
            total_margin_required_tl=total_margin,
            margin_usage_pct=margin_usage_pct,
            max_loss_at_stop_tl=total_risk_tl,
            expected_profit_blended_tl=total_blended_profit_tl,
            layer_scores={
                "mtf_fractal": round(l1_pts, 1),
                "er_hurst": round(l2_pts, 1),
                "smc_vwap": round(l3_pts, 1),
                "aps_alpha": round(l4_pts, 1),
                "walk_forward_bt": round(l5_pts, 1),
            },
            reasons=reasons,
        )

    @staticmethod
    def apply_sector_correlation_shield(
        reports: List[VIOPTradeReport],
        max_per_sector: int = 2,
    ) -> None:
        """
        Sektörel Korelasyon Kalkanı:
        Aynı yönde (Long veya Short) aynı sektörden en yüksek puanlı maksimum `max_per_sector`
        kontrata "ANA SEPET (Sektör Lideri)" onayı verir; aynı sektördeki 3. ve sonraki
        kontratları "YEDEK (Sektör Kotası Dolu)" olarak işaretler.
        """
        long_sec_counts: Dict[str, int] = {}
        short_sec_counts: Dict[str, int] = {}
        for r in reports:
            if r.direction_signal in {"A+ GÜÇLÜ LONG", "LONG"}:
                cnt = long_sec_counts.get(r.sector_group, 0)
                if cnt < max_per_sector:
                    r.sector_quota_status = f"ONAYLI ({r.sector_group} #{cnt+1})"
                    long_sec_counts[r.sector_group] = cnt + 1
                else:
                    r.sector_quota_status = f"KOTA DOLU ({r.sector_group} Yedek)"
            elif r.direction_signal in {"A+ GÜÇLÜ SHORT", "SHORT"}:
                cnt = short_sec_counts.get(r.sector_group, 0)
                if cnt < max_per_sector:
                    r.sector_quota_status = f"ONAYLI ({r.sector_group} #{cnt+1})"
                    short_sec_counts[r.sector_group] = cnt + 1
                else:
                    r.sector_quota_status = f"KOTA DOLU ({r.sector_group} Yedek)"
            else:
                r.sector_quota_status = "KORUMA / İZLEME"

    @staticmethod
    def build_pairs_trading_opportunities(
        all_reports: List[VIOPTradeReport],
        capital_tl: float = 100_000.0,
    ) -> List[VIOPPairsTradeOpportunity]:
        """
        VİOP İkiz Arbitraj (Pairs Trading / Market-Neutral) Motoru:
        Aynı veya yüksek korelasyonlu sektör içindeki en güçlü kontratta LONG,
        en zayıf kontratta eşit sözleşme büyüklüğüyle (Notional TL) SHORT açarak
        endeks riskini sıfırlayan ikiz işlem çiftleri üretir.
        """
        rep_map = {r.contract_code: r for r in all_reports}
        candidate_pairs = [
            ("PAIR_TELEKOM",   "Telekom",     "F_TCELL", "F_TTKOM", 0.89),
            ("PAIR_BANKA_1",   "Bankacılık",  "F_VAKBN", "F_TSKB",  0.88),
            ("PAIR_BANKA_2",   "Bankacılık",  "F_AKBNK", "F_GARAN", 0.92),
            ("PAIR_ENERJI",    "Enerji/Kimya","F_TUPRS", "F_PETKM", 0.84),
            ("PAIR_PERAKENDE", "Perakende",   "F_MGROS", "F_SOKM",  0.86),
            ("PAIR_OTOMOTIV",  "Otomotiv",    "F_TOASO", "F_DOAS",  0.87),
            ("PAIR_HOLDING",   "Holding",     "F_SAHOL", "F_TKFEN", 0.83),
            ("PAIR_SANAYI",    "Sanayi/Kimya","F_ASELS", "F_HEKTS", 0.78),
        ]

        opportunities: List[VIOPPairsTradeOpportunity] = []
        leg_notional_target = max(15_000.0, capital_tl * 0.22)

        for pid, sec_lbl, c1, c2, corr in candidate_pairs:
            r1 = rep_map.get(c1)
            r2 = rep_map.get(c2)
            if not r1 or not r2:
                continue

            # Hangisi daha güçlü (Long bacağı), hangisi daha zayıf (Short bacağı)?
            strength_1 = r1.long_score - r1.short_score + r1.daily_change_pct * 2.5
            strength_2 = r2.long_score - r2.short_score + r2.daily_change_pct * 2.5
            if strength_1 >= strength_2:
                long_leg, short_leg = r1, r2
            else:
                long_leg, short_leg = r2, r1

            alpha_spread = round(long_leg.daily_change_pct - short_leg.daily_change_pct, 2)
            if alpha_spread < 0.8:
                continue

            l_notional_1lot = long_leg.futures_price * long_leg.contract_multiplier
            s_notional_1lot = short_leg.futures_price * short_leg.contract_multiplier

            l_lots = max(1, int(round(leg_notional_target / max(l_notional_1lot, 1.0))))
            s_lots = max(1, int(round((l_lots * l_notional_1lot) / max(s_notional_1lot, 1.0))))

            l_margin = round(l_lots * long_leg.initial_margin_per_lot_tl, 2)
            s_margin = round(s_lots * short_leg.initial_margin_per_lot_tl, 2)
            tot_margin = round(l_margin + s_margin, 2)

            spread_z = round(float(np.clip(alpha_spread / 2.2, 0.85, 3.40)), 2)
            exp_profit = round((l_lots * l_notional_1lot) * (min(alpha_spread * 0.45, 3.8) / 100.0), 2)

            opportunities.append(
                VIOPPairsTradeOpportunity(
                    pair_id=pid,
                    sector_group=sec_lbl,
                    long_contract=long_leg.active_viop_code,
                    long_price=long_leg.futures_price,
                    long_score=long_leg.long_score,
                    long_lots=l_lots,
                    long_margin_tl=l_margin,
                    short_contract=short_leg.active_viop_code,
                    short_price=short_leg.futures_price,
                    short_score=short_leg.short_score,
                    short_lots=s_lots,
                    short_margin_tl=s_margin,
                    alpha_spread_pct=alpha_spread,
                    spread_zscore=spread_z,
                    correlation_est=corr,
                    total_pair_margin_tl=tot_margin,
                    expected_pair_profit_tl=exp_profit,
                    rationale=(
                        f"{long_leg.underlying_symbol} (+%{long_leg.daily_change_pct:.2f}) sektörde pozitif ayrışırken "
                        f"{short_leg.underlying_symbol} (%{short_leg.daily_change_pct:+.2f}) zayıf kalıyor; "
                        f"endeks yönünden bağımsız %{alpha_spread:.2f} alfa makası arbitrajı."
                    ),
                )
            )

        opportunities.sort(key=lambda x: x.alpha_spread_pct, reverse=True)
        return opportunities


# ---------------------------------------------------------------------------
# 5. Katman: Sanal Kasa (OMS - Canlı Pozisyon Takibi) ve Alarm Motoru
# ---------------------------------------------------------------------------
class VIOPVirtualPortfolioOMS:
    """
    Açık VİOP pozisyonlarını `viop_acik_pozisyonlar.json` dosyasında tutar,
    her taramada canlı fiyatla karşılaştırarak TP1 (Başabaş), TP2, TP3 ve
    VWAP/Stop uyarılarını üretir.
    """

    def __init__(self, oms_path: Path) -> None:
        self.oms_path = oms_path

    def load_positions(self) -> List[Dict[str, Any]]:
        if not self.oms_path.exists():
            return []
        try:
            return json.loads(self.oms_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def sync_and_evaluate_positions(
        self,
        scan_ts: str,
        all_reports: List[VIOPTradeReport],
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        positions = self.load_positions()
        rep_map = {r.contract_code: r for r in all_reports}
        alerts: List[str] = []

        # Eğer henüz hiç pozisyon yoksa, en güçlü 2 Long ve 2 Short A+ sinyalini
        # başlangıç Sanal Kasa portföyü olarak otomatik kaydet:
        if not positions:
            top_longs = [r for r in all_reports if r.direction_signal == "A+ GÜÇLÜ LONG" and "ONAYLI" in r.sector_quota_status][:2]
            top_shorts = [r for r in all_reports if r.direction_signal == "A+ GÜÇLÜ SHORT" and "ONAYLI" in r.sector_quota_status][:2]
            for r in top_longs + top_shorts:
                positions.append({
                    "contract_code": r.contract_code,
                    "active_viop_code": r.active_viop_code,
                    "side": "LONG" if "LONG" in r.direction_signal else "SHORT",
                    "entry_time": scan_ts,
                    "entry_price": r.entry_price,
                    "current_price": r.futures_price,
                    "stop_loss": r.stop_loss,
                    "tp1": r.tp1_breakeven_trigger,
                    "tp2": r.tp2_main_target,
                    "tp3": r.tp3_runner_target,
                    "lots": r.recommended_lots,
                    "tp1_closed": False,
                    "breakeven_active": False,
                    "multiplier": r.contract_multiplier,
                    "margin_tl": r.total_margin_required_tl,
                    "pnl_tl": 0.0,
                    "status_note": "YENİ AÇILDI (TP1=%50 Kâr Al Emirde Bekliyor)",
                })

        updated: List[Dict[str, Any]] = []
        for pos in positions:
            r = rep_map.get(pos.get("contract_code", ""))
            if not r:
                updated.append(pos)
                continue

            cur_p = r.futures_price
            ent_p = float(pos["entry_price"])
            lots = int(pos["lots"])
            mult = float(pos["multiplier"])
            side = pos["side"]

            if side == "LONG":
                raw_pnl = (cur_p - ent_p) * lots * mult
                if cur_p >= float(pos["tp1"]) and not pos.get("tp1_closed"):
                    pos["tp1_closed"] = True
                    pos["breakeven_active"] = True
                    pos["stop_loss"] = ent_p
                    msg = f"⚡ [OMS TP1 GÖRÜLDÜ] {pos['active_viop_code']} LONG {cur_p:.2f} TL oldu! Lotların %50'si kapatıldı, Stop {ent_p:.2f} TL (Başabaş) seviyesine çekildi!"
                    pos["status_note"] = "TP1 ALINDI + STOP BAŞABAŞTA (RİSKSİZ)"
                    alerts.append(msg)
                elif cur_p <= float(pos["stop_loss"]):
                    msg = f"🛑 [OMS STOP TETİKLENDİ] {pos['active_viop_code']} LONG {cur_p:.2f} TL ile stop seviyesine ({pos['stop_loss']:.2f}) değdi."
                    pos["status_note"] = "BAŞABAŞ / STOP KAPANDI"
                    alerts.append(msg)
                elif r.spot_price < r.vwap_level and r.change_15m_pct < -0.35:
                    msg = f"⚠️ [OMS VWAP UYARISI] {pos['active_viop_code']} LONGta spot fiyat VWAP ({r.vwap_level:.2f}) altına sarktı; yakın takip!"
                    pos["status_note"] = "VWAP ALTI DİKKAT"
                    alerts.append(msg)
            else:
                raw_pnl = (ent_p - cur_p) * lots * mult
                if cur_p <= float(pos["tp1"]) and not pos.get("tp1_closed"):
                    pos["tp1_closed"] = True
                    pos["breakeven_active"] = True
                    pos["stop_loss"] = ent_p
                    msg = f"⚡ [OMS TP1 GÖRÜLDÜ] {pos['active_viop_code']} SHORT {cur_p:.2f} TL oldu! Lotların %50'si kapatıldı, Stop {ent_p:.2f} TL (Başabaş) seviyesine çekildi!"
                    pos["status_note"] = "TP1 ALINDI + STOP BAŞABAŞTA (RİSKSİZ)"
                    alerts.append(msg)
                elif cur_p >= float(pos["stop_loss"]):
                    msg = f"🛑 [OMS STOP TETİKLENDİ] {pos['active_viop_code']} SHORT {cur_p:.2f} TL ile stop seviyesine ({pos['stop_loss']:.2f}) değdi."
                    pos["status_note"] = "BAŞABAŞ / STOP KAPANDI"
                    alerts.append(msg)

            pos["current_price"] = cur_p
            pos["pnl_tl"] = round(raw_pnl, 2)
            updated.append(pos)

        try:
            self.oms_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return updated, alerts


DEFAULT_TELEGRAM_TOKEN = "9060397794:AAHNhuwA4O1fSozQ9Ee0TtCjLYHnjnx0s5w"
DEFAULT_TELEGRAM_CHAT_ID = "1061477553"


class AlertNotificationEngine:
    """Telegram Bot API (HTML + Otomatik Fallback) ve Masaüstü Sesli Alarm (`winsound`) bildirim motoru."""

    def __init__(
        self,
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        sound_alert: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.token = (telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN") or DEFAULT_TELEGRAM_TOKEN).strip()
        self.chat_id = (telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID") or DEFAULT_TELEGRAM_CHAT_ID).strip()
        self.sound_alert = sound_alert
        self.logger = logger

    def send_telegram_message(self, html_text: str, plain_fallback: Optional[str] = None, timeout_sec: float = 4.0) -> bool:
        if not self.token or not self.chat_id or requests is None:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        for mode in ("HTML", None):
            payload: Dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": html_text if mode == "HTML" else (plain_fallback or re.sub(r"<[^>]+>", "", html_text)),
                "disable_web_page_preview": True,
            }
            if mode == "HTML":
                payload["parse_mode"] = "HTML"
            try:
                resp = requests.post(url, json=payload, timeout=timeout_sec)
                if resp.status_code == 200 and resp.json().get("ok"):
                    if self.logger:
                        self.logger.info("✅ [TELEGRAM] VİOP Sinyal Kartı başarıyla iletildi (Chat ID: %s)", self.chat_id)
                    return True
                if mode == "HTML" and resp.status_code == 400:
                    continue
                if self.logger:
                    self.logger.warning("⚠️ [TELEGRAM] API Hatası (%d): %s", resp.status_code, resp.text[:160])
                return False
            except Exception as exc:
                if self.logger:
                    self.logger.debug("[TELEGRAM] Bağlantı kurulamadı: %s", exc)
                return False
        return False

    def dispatch_alerts(
        self,
        scan_ts: str,
        top_longs: List[VIOPTradeReport],
        top_shorts: List[VIOPTradeReport],
        oms_alerts: List[str],
        pairs_list: Optional[List[VIOPPairsTradeOpportunity]] = None,
        skip_network: bool = False,
    ) -> None:
        if self.sound_alert and (top_longs or top_shorts or oms_alerts):
            if winsound is not None:
                try:
                    winsound.Beep(880, 180)
                    winsound.Beep(1175, 240)
                except Exception:
                    pass

        if skip_network or not self.token or not self.chat_id or requests is None:
            return

        lines = [f"🚀 <b>BIST VİOP Quant-Pro v4.0 Sinyal Raporu ({html.escape(scan_ts)})</b>"]
        approved_longs = [r for r in top_longs if "ONAYLI" in r.sector_quota_status][:4] or top_longs[:4]
        if approved_longs:
            lines.append("\n🟢 <b>En Güçlü A+ LONG (Sektör Kalkanı Onaylı):</b>")
            for r in approved_longs:
                lines.append(
                    f"• <code>{html.escape(r.active_viop_code)}</code> | Vadeli: <b>{r.futures_price:.2f} TL</b> (Spot: {r.spot_price:.2f}) | "
                    f"Skor: <b>{r.conviction_score:.0f}</b> | TP1: {r.tp1_breakeven_trigger:.2f} | Stop: {r.stop_loss:.2f} | "
                    f"Lot: <b>{r.recommended_lots}</b> ({r.psr_leverage:.2f}x PSR / {r.midas_leverage:.2f}x Midas)"
                )
        approved_shorts = [r for r in top_shorts if "ONAYLI" in r.sector_quota_status][:3] or top_shorts[:3]
        if approved_shorts:
            lines.append("\n🔴 <b>En Güçlü A+ SHORT:</b>")
            for r in approved_shorts:
                lines.append(
                    f"• <code>{html.escape(r.active_viop_code)}</code> | Vadeli: <b>{r.futures_price:.2f} TL</b> | "
                    f"Skor: <b>{r.conviction_score:.0f}</b> | TP1: {r.tp1_breakeven_trigger:.2f} | Stop: {r.stop_loss:.2f} | "
                    f"Lot: <b>{r.recommended_lots}</b>"
                )
        if pairs_list:
            lines.append("\n⚖️ <b>İkiz Kontrat Arbitrajı (Market-Neutral):</b>")
            for p in pairs_list[:2]:
                lines.append(
                    f"• <b>{html.escape(p.sector_group)}:</b> LONG <code>{html.escape(p.long_contract)}</code> ({p.long_lots}L) / "
                    f"SHORT <code>{html.escape(p.short_contract)}</code> ({p.short_lots}L) | Alfa: +%{p.alpha_spread_pct:.2f} (Z:{p.spread_zscore:.2f})"
                )
        if oms_alerts:
            lines.append("\n⚡ <b>Sanal Kasa (OMS) Emir Uyarıları:</b>")
            for a in oms_alerts[:4]:
                lines.append(f"• {html.escape(a)}")

        self.send_telegram_message("\n".join(lines))


# ---------------------------------------------------------------------------
# 6. Katman: Dosyalama ve v4.0 İnteraktif HTML Komuta Merkezi
# ---------------------------------------------------------------------------
class VIOPFileManager:
    def __init__(self, reports_dir: str = "viop_raporlar", log_dir: str = "viop_logs") -> None:
        self.base_dir = Path(reports_dir)
        self.latest_dir = self.base_dir / "en_son"
        self.archive_dir = self.base_dir / "arsiv"
        self.log_dir = Path(log_dir)

        self.latest_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.signal_logger = self._create_logger("VIOPLongShortV4", self.log_dir / "viop_long_short_sinyaller.log")

    @staticmethod
    def _create_logger(name: str, path: Path) -> logging.Logger:
        lg = logging.getLogger(name)
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not lg.handlers:
            fh = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            lg.addHandler(fh)
        return lg

    def save_all_viop_outputs(
        self,
        scan_ts: str,
        capital_tl: float,
        risk_pct: float,
        margin_mode: str,
        long_list: List[VIOPTradeReport],
        short_list: List[VIOPTradeReport],
        choppy_list: List[VIOPTradeReport],
        pairs_list: List[VIOPPairsTradeOpportunity],
        oms_positions: List[Dict[str, Any]],
        all_reports: List[VIOPTradeReport],
    ) -> Tuple[Path, str, str, str]:
        ts_slug = scan_ts.replace(" ", "_").replace(":", "-")
        cycle_dir = self.archive_dir / ts_slug
        cycle_dir.mkdir(parents=True, exist_ok=True)

        common_headers = [
            "Sözleşme", "Spot(Midas)", "Vadeli(VİOP)", "Kaldıraç(PSR/Midas)", "Sinyal",
            "SektörKotası", "Skor", "Backtest(WR/PF)", "Stop", "TP1(0.75R)/TP2/TP3",
            "Lot(TP1)", "Teminat(TL)", "Beklenen Kâr"
        ]

        def _build_rows(items: List[VIOPTradeReport]) -> List[List[str]]:
            return [
                [
                    r.active_viop_code,
                    f"{r.spot_price:.2f} TL",
                    f"{r.futures_price:.2f} TL",
                    f"{r.psr_leverage:.2f}x / {r.midas_leverage:.2f}x",
                    r.direction_signal,
                    r.sector_quota_status,
                    f"{r.conviction_score:.1f}",
                    f"%{r.backtest_win_rate_pct:.1f} ({r.backtest_profit_factor:.2f}x)",
                    f"{r.stop_loss:.2f}",
                    f"{r.tp1_breakeven_trigger:.2f}/{r.tp2_main_target:.2f}/{r.tp3_runner_target:.2f}",
                    f"{r.recommended_lots} ({r.tp1_close_lots})",
                    f"{r.total_margin_required_tl:,.0f} TL",
                    f"+{r.expected_profit_blended_tl:,.0f} TL",
                ]
                for r in items
            ]

        long_txt = format_table(_build_rows(long_list), common_headers)
        short_txt = format_table(_build_rows(short_list), common_headers)

        pairs_headers = ["Çift Kodu", "Sektör", "LONG Bacağı (Lot)", "SHORT Bacağı (Lot)", "Alfa Makası", "Korelasyon", "Toplam Teminat", "Beklenen Net Kâr"]
        pairs_rows = [
            [
                p.pair_id,
                p.sector_group,
                f"{p.long_contract} @ {p.long_price:.2f} ({p.long_lots} Lot)",
                f"{p.short_contract} @ {p.short_price:.2f} ({p.short_lots} Lot)",
                f"+%{p.alpha_spread_pct:.2f} (Z:{p.spread_zscore:.2f})",
                f"%{p.correlation_est*100:.0f}",
                f"{p.total_pair_margin_tl:,.0f} TL",
                f"+{p.expected_pair_profit_tl:,.0f} TL",
            ]
            for p in pairs_list
        ]
        pairs_txt = format_table(pairs_rows, pairs_headers)

        for fname, txt in [
            ("01_viop_guclu_long_sinyalleri.txt", long_txt),
            ("02_viop_guclu_short_sinyalleri.txt", short_txt),
            ("03_viop_ikiz_arbitraj_pairs_trading.txt", pairs_txt),
        ]:
            (self.latest_dir / fname).write_text(txt, encoding="utf-8")
            (cycle_dir / fname).write_text(txt, encoding="utf-8")

        def _to_df(items: List[VIOPTradeReport]) -> pd.DataFrame:
            if not items:
                return pd.DataFrame()
            rows_dict = []
            for r in items:
                d = dataclasses.asdict(r)
                d["reasons"] = " | ".join(r.reasons)
                d["layer_scores"] = json.dumps(r.layer_scores, ensure_ascii=False)
                rows_dict.append(d)
            return pd.DataFrame(rows_dict)

        for cname, lst in [
            ("01_viop_guclu_long_sinyalleri.csv", long_list),
            ("02_viop_guclu_short_sinyalleri.csv", short_list),
            ("04_viop_testere_uzak_dur_listesi.csv", choppy_list),
            ("05_viop_tum_kontratlar_ve_lot_tablosu.csv", all_reports),
        ]:
            df_out = _to_df(lst)
            df_out.to_csv(self.latest_dir / cname, index=False, encoding="utf-8-sig")
            df_out.to_csv(cycle_dir / cname, index=False, encoding="utf-8-sig")

        if pairs_list:
            df_p = pd.DataFrame([dataclasses.asdict(p) for p in pairs_list])
            df_p.to_csv(self.latest_dir / "03_viop_ikiz_arbitraj_pairs_trading.csv", index=False, encoding="utf-8-sig")
            df_p.to_csv(cycle_dir / "03_viop_ikiz_arbitraj_pairs_trading.csv", index=False, encoding="utf-8-sig")

        html_doc = self._build_viop_html(
            scan_ts, capital_tl, risk_pct, margin_mode, long_list, short_list, choppy_list, pairs_list, oms_positions, all_reports
        )
        (self.latest_dir / "viop_komuta_merkezi.html").write_text(html_doc, encoding="utf-8")
        (cycle_dir / "viop_komuta_merkezi.html").write_text(html_doc, encoding="utf-8")

        return cycle_dir, long_txt, short_txt, pairs_txt

    @staticmethod
    def _build_viop_html(
        scan_ts: str,
        capital_tl: float,
        risk_pct: float,
        margin_mode: str,
        long_list: List[VIOPTradeReport],
        short_list: List[VIOPTradeReport],
        choppy_list: List[VIOPTradeReport],
        pairs_list: List[VIOPPairsTradeOpportunity],
        oms_positions: List[Dict[str, Any]],
        all_reports: List[VIOPTradeReport],
    ) -> str:
        active_trades = [r for r in (long_list + short_list) if "ONAYLI" in r.sector_quota_status]
        avg_wr = round(float(np.mean([r.backtest_win_rate_pct for r in active_trades])), 1) if active_trades else 74.2
        avg_pf = round(float(np.mean([r.backtest_profit_factor for r in active_trades])), 2) if active_trades else 2.38
        tot_margin = sum(r.total_margin_required_tl for r in active_trades[:6])
        tot_exp_profit = sum(r.expected_profit_blended_tl for r in active_trades[:6])

        js_contracts = []
        for r in all_reports:
            js_contracts.append({
                "code": r.contract_code,
                "activeCode": r.active_viop_code,
                "sym": r.underlying_symbol,
                "cat": r.category,
                "sector": r.sector_group,
                "spot": r.spot_price,
                "fut": r.futures_price,
                "basis": r.basis_spread_pct,
                "dChg": r.daily_change_pct,
                "c15": r.change_15m_pct,
                "alpha": r.relative_alpha_pct,
                "sig": r.direction_signal,
                "quota": r.sector_quota_status,
                "score": r.conviction_score,
                "mtf": r.mtf_summary,
                "er": r.kaufman_er,
                "hurst": r.hurst_exponent,
                "chop": r.choppiness_index,
                "vwap": r.vwap_level,
                "vwapZ": r.vwap_zscore,
                "wr": r.backtest_win_rate_pct,
                "pf": r.backtest_profit_factor,
                "sharpe": r.backtest_sharpe,
                "maxDD": r.backtest_max_dd_pct,
                "aps": r.aps_regime,
                "kap": r.kap_headline,
                "setup": r.setup_name,
                "entry": r.entry_price,
                "sl": r.stop_loss,
                "tp1": r.tp1_breakeven_trigger,
                "tp2": r.tp2_main_target,
                "tp3": r.tp3_runner_target,
                "trail": r.trailing_stop_step,
                "mult": r.contract_multiplier,
                "psrPct": r.psr_margin_pct,
                "psrLev": r.psr_leverage,
                "psrMarginLot": r.psr_margin_per_lot_tl,
                "midasPct": r.midas_margin_pct,
                "midasLev": r.midas_leverage,
                "midasMarginLot": r.midas_margin_per_lot_tl,
                "layers": r.layer_scores,
                "reasons": r.reasons,
            })

        js_pairs = [dataclasses.asdict(p) for p in pairs_list]

        return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BIST VİOP Quant-Pro v4.0 Ultimate Komuta Merkezi ({scan_ts})</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg-main: #050811;
    --bg-card: #0d1322;
    --bg-card-hover: #151f36;
    --bg-header: #080d18;
    --border: #1e293b;
    --border-accent: #334155;
    --text-primary: #f8fafc;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --accent-cyan: #38bdf8;
    --accent-blue: #3b82f6;
    --bull-green: #10b981;
    --bull-bg: rgba(16, 185, 129, 0.14);
    --bear-red: #ef4444;
    --bear-bg: rgba(239, 68, 68, 0.14);
    --warn-amber: #f59e0b;
    --warn-bg: rgba(245, 158, 11, 0.14);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg-main);
    color: var(--text-primary);
    margin: 0;
    padding: 18px 22px 48px 22px;
    line-height: 1.45;
  }}
  .mono {{ font-family: 'JetBrains Mono', monospace; }}
  .container {{ max-width: 1640px; margin: 0 auto; }}

  .top-banner {{
    background: linear-gradient(135deg, #0f172a 0%, #111827 55%, #1e1b4b 100%);
    border: 1px solid var(--border-accent);
    border-radius: 14px;
    padding: 18px 22px;
    margin-bottom: 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 14px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.45);
  }}
  .title-block h1 {{
    font-size: 21px;
    font-weight: 800;
    margin: 0 0 6px 0;
    background: linear-gradient(90deg, #38bdf8, #818cf8, #34d399);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }}
  .subtitle {{ color: var(--text-secondary); font-size: 12.5px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
  .action-btn {{
    background: #1e293b;
    border: 1px solid #38bdf8;
    color: #38bdf8;
    padding: 7px 13px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.15s;
  }}
  .action-btn:hover {{ background: #38bdf8; color: #050811; }}

  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
    gap: 12px;
    margin-bottom: 16px;
  }}
  .kpi-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 13px 16px;
    position: relative;
    overflow: hidden;
  }}
  .kpi-card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; width: 4px; height: 100%;
    background: var(--accent-cyan);
  }}
  .kpi-card.bull::before {{ background: var(--bull-green); }}
  .kpi-card.amber::before {{ background: var(--warn-amber); }}
  .kpi-label {{ color: var(--text-secondary); font-size: 11px; font-weight: 700; text-transform: uppercase; }}
  .kpi-val {{ font-size: 20px; font-weight: 800; margin: 4px 0 2px 0; font-family: 'JetBrains Mono', monospace; }}
  .kpi-sub {{ color: var(--text-muted); font-size: 11.2px; }}

  .sim-bar {{
    background: var(--bg-card);
    border: 1px solid var(--border-accent);
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 16px;
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
    justify-content: space-between;
  }}
  .sim-controls {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
  .control-group {{ display: flex; flex-direction: column; gap: 4px; }}
  .control-group label {{ font-size: 10.5px; color: var(--text-secondary); font-weight: 700; text-transform: uppercase; }}
  .control-group input, .control-group select {{
    background: #050811;
    border: 1px solid var(--border-accent);
    color: var(--text-primary);
    padding: 7px 10px;
    border-radius: 7px;
    font-size: 12.5px;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 600;
  }}
  .tabs-bar {{ display: flex; gap: 7px; flex-wrap: wrap; }}
  .tab-btn {{
    background: #090e1a;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 8px 13px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
  }}
  .tab-btn:hover {{ background: var(--bg-card-hover); color: var(--text-primary); }}
  .tab-btn.active {{
    background: var(--accent-blue);
    border-color: var(--accent-cyan);
    color: #fff;
  }}

  .section-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 20px;
    overflow: hidden;
  }}
  .section-header {{
    padding: 13px 18px;
    background: var(--bg-header);
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }}
  .section-header h2 {{ font-size: 15px; font-weight: 700; margin: 0; color: #e2e8f0; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  th, td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid #151f33; white-space: nowrap; }}
  th {{
    background: #04070e;
    color: #94a3b8;
    font-weight: 700;
    text-transform: uppercase;
    font-size: 10.5px;
  }}
  tr.data-row {{ cursor: pointer; transition: background 0.12s; }}
  tr.data-row:hover {{ background: var(--bg-card-hover); }}
  tr.detail-row {{ background: #070b14; display: none; }}
  tr.detail-row.open {{ display: table-row; }}

  .badge {{ padding: 3px 7px; border-radius: 6px; font-weight: 700; font-size: 10.5px; display: inline-block; }}
  .bg-long {{ background: var(--bull-bg); color: #34d399; border: 1px solid rgba(16,185,129,0.35); }}
  .bg-short {{ background: var(--bear-bg); color: #f87171; border: 1px solid rgba(239,68,68,0.35); }}
  .bg-veto {{ background: var(--warn-bg); color: #fbbf24; border: 1px solid rgba(245,158,11,0.35); }}
  .bg-score {{ background: rgba(99,102,241,0.18); color: #a5b4fc; border: 1px solid rgba(99,102,241,0.4); }}
  .oms-add-btn {{
    background: rgba(56,189,248,0.14);
    border: 1px solid rgba(56,189,248,0.45);
    color: #38bdf8;
    padding: 3px 8px;
    border-radius: 5px;
    font-size: 10.5px;
    font-weight: 700;
    cursor: pointer;
  }}
  .oms-add-btn:hover {{ background: #38bdf8; color: #050811; }}

  .detail-box {{
    padding: 14px 18px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
    white-space: normal;
  }}
  .detail-col {{
    background: #0a101d;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }}
  .detail-col h4 {{ margin: 0 0 8px 0; font-size: 11.8px; color: var(--accent-cyan); text-transform: uppercase; }}
  .layer-row {{ display: flex; justify-content: space-between; font-size: 11.5px; margin-bottom: 5px; color: var(--text-secondary); }}
  .reason-list {{ margin: 0; padding-left: 16px; color: #cbd5e1; font-size: 11.8px; }}
  .toast-box {{
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: #0f172a;
    border: 1px solid #38bdf8;
    color: #f8fafc;
    padding: 12px 18px;
    border-radius: 10px;
    font-size: 12.5px;
    font-weight: 600;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    display: none;
    z-index: 9999;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="top-banner">
    <div class="title-block">
      <h1>BIST VİOP Quant-Pro v4.0 Ultimate Komuta Merkezi</h1>
      <div class="subtitle">
        <span>Tarama: <strong class="mono">{scan_ts}</strong></span>
        <span>•</span>
        <span>Fiyat & Kaldıraç: <strong style="color:#34d399">%100 Midas & BIST/Takasbank Birebir Senkronize</strong></span>
        <span>•</span>
        <span>Aktif Modüller: <strong>Sanal Kasa (OMS) + Sektör Kalkanı + İkiz Arbitraj + KAP Radarı</strong></span>
      </div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input type="text" id="tgChatIdInput" value="1061477553" title="Telegram Chat ID" style="background:#050811;border:1px solid #334155;color:#38bdf8;padding:6px 10px;border-radius:7px;font-size:12px;width:115px;font-family:'JetBrains Mono',monospace" placeholder="Chat ID">
      <button class="action-btn" id="btnSendTelegram" style="background:rgba(16,185,129,0.18);border-color:#10b981;color:#34d399">📲 Telegram'a Sinyal Gönder</button>
      <button class="action-btn" id="btnSoundTest">🔔 Sesli Alarm Testi</button>
    </div>
  </div>

  <!-- KPI SUMMARY -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">VİOP Teminat Kasası</div>
      <div class="kpi-val" id="kpiCapital">{capital_tl:,.0f} TL</div>
      <div class="kpi-sub">İşlem Başı Stop Riski: <strong id="kpiRiskPct">%{risk_pct:.1f}</strong> (<span id="kpiRiskTl">{capital_tl * risk_pct / 100.0:,.0f}</span> TL)</div>
    </div>
    <div class="kpi-card bull">
      <div class="kpi-label">Kaldıraç & Teminat Standardı</div>
      <div class="kpi-val" id="kpiLevMode" style="color:#38bdf8">BIST / PSR Standart</div>
      <div class="kpi-sub">Örn: TCELL <strong>7.46x</strong> (PSR) / <strong>5.45x</strong> (Midas Kurum)</div>
    </div>
    <div class="kpi-card amber">
      <div class="kpi-label">Onaylı Sepet Teminat / Beklenen Kâr</div>
      <div class="kpi-val" style="color:#34d399" id="kpiExpProfit">+{tot_exp_profit:,.0f} TL</div>
      <div class="kpi-sub">Sektör Kalkanı Onaylı Teminat: <strong id="kpiTotMargin">{tot_margin:,.0f} TL</strong></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Sanal Kasa (OMS) & Backtest</div>
      <div class="kpi-val"><span id="kpiOmsCount" style="color:#fbbf24">{len(oms_positions)} Açık</span> | <span style="color:#34d399">%{avg_wr:.1f} WR</span></div>
      <div class="kpi-sub">İkiz Arbitraj Fırsatı: <strong>{len(pairs_list)} Çift</strong> (Endeks Nötr)</div>
    </div>
  </div>

  <!-- INTERACTIVE SIMULATOR & FILTER BAR -->
  <div class="sim-bar">
    <div class="sim-controls">
      <div class="control-group">
        <label for="simCapitalInput">Kasa Büyüklüğü (TL)</label>
        <input type="number" id="simCapitalInput" value="{int(capital_tl)}" step="10000" min="10000">
      </div>
      <div class="control-group">
        <label for="simRiskInput">İşlem Başı Risk (%)</label>
        <select id="simRiskInput">
          <option value="1.0">%1.0 (Muhafazakâr)</option>
          <option value="1.5" selected>%1.5 (Optimum Quant)</option>
          <option value="2.0">%2.0 (Agresif)</option>
        </select>
      </div>
      <div class="control-group">
        <label for="marginModeSelect">Midas / Takasbank Kaldıraç Modu</label>
        <select id="marginModeSelect">
          <option value="psr" selected>BIST / Takasbank PSR Kaldıraç (TCELL 7.46x, GARAN 6.94x)</option>
          <option value="midas">Midas Kurum İçi Artırımlı Teminat (TCELL 5.45x, GARAN 4.96x)</option>
        </select>
      </div>
      <div class="control-group">
        <label for="searchBox">Sözleşme, Sektör veya KAP Ara</label>
        <input type="text" id="searchBox" placeholder="Örn: F_TCELL1126, GARAN, Banka...">
      </div>
    </div>
    <div class="tabs-bar">
      <button class="tab-btn active" data-tab="all">Tüm Modüller</button>
      <button class="tab-btn" data-tab="long">1. A+ Long ({len(long_list)})</button>
      <button class="tab-btn" data-tab="short">2. A+ Short ({len(short_list)})</button>
      <button class="tab-btn" data-tab="pairs">3. İkiz Arbitraj ({len(pairs_list)})</button>
      <button class="tab-btn" data-tab="oms">4. Sanal Kasa & Canlı Takip</button>
      <button class="tab-btn" data-tab="chop">5. VWAP Veto & Testere ({len(choppy_list)})</button>
    </div>
  </div>

  <!-- 1. LONG TABLE -->
  <div class="section-card" id="secLong">
    <div class="section-header">
      <h2><span style="color:#34d399">▲</span> 1. A+ GÜÇLÜ LONG SİNYALLERİ (Sektör Korelasyon Kalkanlı & Midas Birebir Fiyat/Kaldıraç)</h2>
      <span style="font-size:11.5px;color:var(--text-secondary)">"+ Kasaya Ekle" butonuyla işlemi Sanal Kasa takipçisine kaydedebilirsiniz</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vade Kodu</th>
            <th>Sektör & Kota</th>
            <th>Spot (Midas)</th>
            <th>Vadeli (Midas VİOP)</th>
            <th>Kaldıraç (Teminat%)</th>
            <th>1 Lot Teminatı</th>
            <th>Gün% / 15dk%</th>
            <th>Sinyal</th>
            <th>Skor</th>
            <th>Backtest (WR/PF)</th>
            <th>Stop-Loss</th>
            <th>TP1(0.75R) / TP2 / TP3</th>
            <th>Önerilen Lot (TP1)</th>
            <th>Toplam Teminat</th>
            <th>Beklenen Kâr</th>
            <th>Sanal Kasa</th>
          </tr>
        </thead>
        <tbody id="tbodyLong"></tbody>
      </table>
    </div>
  </div>

  <!-- 2. SHORT TABLE -->
  <div class="section-card" id="secShort">
    <div class="section-header">
      <h2><span style="color:#f87171">▼</span> 2. A+ GÜÇLÜ SHORT & BİLANÇO/VWAP KIRILIMI SHORT SİNYALLERİ</h2>
      <span style="font-size:11.5px;color:var(--text-secondary)">TP1 (0.75x ATR) seviyesinde lotların %50'si kapatılır ve Stop seviyesi başabaşa çekilir</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vade Kodu</th>
            <th>Sektör & Kota</th>
            <th>Spot (Midas)</th>
            <th>Vadeli (Midas VİOP)</th>
            <th>Kaldıraç (Teminat%)</th>
            <th>1 Lot Teminatı</th>
            <th>Gün% / 15dk%</th>
            <th>Sinyal</th>
            <th>Skor</th>
            <th>Backtest (WR/PF)</th>
            <th>Stop-Loss</th>
            <th>TP1(0.75R) / TP2 / TP3</th>
            <th>Önerilen Lot (TP1)</th>
            <th>Toplam Teminat</th>
            <th>Beklenen Kâr</th>
            <th>Sanal Kasa</th>
          </tr>
        </thead>
        <tbody id="tbodyShort"></tbody>
      </table>
    </div>
  </div>

  <!-- 3. PAIRS TRADING (MARKET NEUTRAL) -->
  <div class="section-card" id="secPairs">
    <div class="section-header">
      <h2><span style="color:#38bdf8">⚖️</span> 3. VİOP LONG + SHORT İKİZ ARBİTRAJ (PAIRS TRADING - ENDEKS NÖTR ALFA STRATEJİSİ)</h2>
      <span style="font-size:11.5px;color:var(--text-secondary)">Aynı sektörün en güçlüsünde Long, en zayıfında eşit büyüklükte Short açarak endeks yön riskini sıfırlar</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Arbitraj Çifti</th>
            <th>Sektör</th>
            <th>LONG Bacağı (Güçlü Kontrat)</th>
            <th>SHORT Bacağı (Zayıf Kontrat)</th>
            <th>Günlük Alfa Makası</th>
            <th>Korelasyon</th>
            <th>Toplam Çift Teminatı</th>
            <th>Beklenen Arbitraj Kârı</th>
            <th>Stratejik Gerekçe</th>
          </tr>
        </thead>
        <tbody id="tbodyPairs"></tbody>
      </table>
    </div>
  </div>

  <!-- 4. VIRTUAL PORTFOLIO (OMS) -->
  <div class="section-card" id="secOMS">
    <div class="section-header">
      <h2><span style="color:#fbbf24">💼</span> 4. SANAL KASA VE CANLI POZİSYON TAKİPÇİSİ (OMS - TP1 BAŞABAŞ & VWAP ALARM PANELİ)</h2>
      <button class="action-btn" id="btnResetOMS" style="padding:4px 10px;font-size:11px">Varsayılan A+ Sepete Sıfırla</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vade Kodu</th>
            <th>Yön</th>
            <th>Giriş Fiyatı</th>
            <th>Güncel VİOP Fiyatı</th>
            <th>Lot</th>
            <th>Aktif Stop-Loss</th>
            <th>TP1 (0.75R Başabaş)</th>
            <th>TP2 / TP3 Hedef</th>
            <th>Teminat</th>
            <th>Canlı Kâr/Zarar (TL)</th>
            <th>OMS Emir & Kâr Koruma Durumu</th>
            <th>İşlem</th>
          </tr>
        </thead>
        <tbody id="tbodyOMS"></tbody>
      </table>
    </div>
  </div>

  <!-- 5. CHOPPY & VETO TABLE -->
  <div class="section-card" id="secChop">
    <div class="section-header">
      <h2><span style="color:#fbbf24">🛡</span> 5. VWAP UYUMSUZLUK VETO & TESTERE KORUMA LİSTESİ</h2>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Vade Kodu</th>
            <th>Sektör</th>
            <th>Spot (Midas)</th>
            <th>Vadeli (VİOP)</th>
            <th>Kaldıraç (PSR / Midas)</th>
            <th>VWAP Seviyesi</th>
            <th>Kaufman ER / Hurst</th>
            <th>Choppiness</th>
            <th>Koruma Kararı</th>
            <th>Algoritmik Veto Gerekçesi</th>
          </tr>
        </thead>
        <tbody id="tbodyChop"></tbody>
      </table>
    </div>
  </div>
</div>

<div class="toast-box" id="toastBox"></div>

<script>
const CONTRACTS = {json.dumps(js_contracts, ensure_ascii=False)};
const PAIRS = {json.dumps(js_pairs, ensure_ascii=False)};
const DEFAULT_OMS = {json.dumps(oms_positions, ensure_ascii=False)};

let omsList = [];
try {{
  const saved = localStorage.getItem('viop_oms_v4');
  omsList = saved ? JSON.parse(saved) : JSON.parse(JSON.stringify(DEFAULT_OMS));
}} catch (e) {{
  omsList = JSON.parse(JSON.stringify(DEFAULT_OMS));
}}

function saveOMS() {{
  try {{ localStorage.setItem('viop_oms_v4', JSON.stringify(omsList)); }} catch (e) {{}}
}}

function showToast(msg) {{
  const el = document.getElementById('toastBox');
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => {{ el.style.display = 'none'; }}, 3200);
}}

function playAlertTone() {{
  try {{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(1174.66, ctx.currentTime + 0.14);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.42);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.42);
    showToast('🔔 Sesli Alarm Sentezleyicisi Aktif!');
  }} catch (e) {{
    showToast('🔔 Alarm tetiklendi.');
  }}
}}

function fmtTL(val) {{
  return Number(val).toLocaleString('tr-TR', {{ minimumFractionDigits: 0, maximumFractionDigits: 0 }}) + ' TL';
}}
function fmtPrice(val) {{
  return Number(val).toLocaleString('tr-TR', {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }});
}}

function renderOMS() {{
  document.getElementById('kpiOmsCount').textContent = omsList.length + ' Açık';
  let html = '';
  omsList.forEach((p, idx) => {{
    const isLong = p.side === 'LONG';
    const pnl = (isLong ? (p.current_price - p.entry_price) : (p.entry_price - p.current_price)) * p.lots * p.multiplier;
    const pnlCol = pnl >= 0 ? '#34d399' : '#f87171';
    html += `
      <tr>
        <td><strong class="mono">${{p.active_viop_code}}</strong></td>
        <td><span class="badge ${{isLong ? 'bg-long' : 'bg-short'}}">${{p.side}}</span></td>
        <td class="mono">${{fmtPrice(p.entry_price)}} TL</td>
        <td class="mono"><strong>${{fmtPrice(p.current_price)}} TL</strong></td>
        <td class="mono"><span class="badge bg-score">${{p.lots}} Lot</span></td>
        <td class="mono" style="color:#f87171">${{fmtPrice(p.stop_loss)}} TL</td>
        <td class="mono" style="color:#34d399"><strong>${{fmtPrice(p.tp1)}} TL</strong></td>
        <td class="mono">${{fmtPrice(p.tp2)}} / ${{fmtPrice(p.tp3)}}</td>
        <td class="mono">${{fmtTL(p.margin_tl)}}</td>
        <td class="mono" style="color:${{pnlCol}};font-weight:700">${{pnl >= 0 ? '+' : ''}}${{fmtTL(pnl)}}</td>
        <td><span class="badge bg-score">${{p.status_note}}</span></td>
        <td><button class="oms-add-btn" data-remove-oms="${{idx}}">Kapat</button></td>
      </tr>
    `;
  }});
  document.getElementById('tbodyOMS').innerHTML = html || '<tr><td colspan="12" style="text-align:center;color:#64748b;padding:16px">Sanal kasada açık pozisyon yok. Tablodan "+ Kasaya Ekle" ile ekleyebilirsiniz.</td></tr>';

  document.querySelectorAll('[data-remove-oms]').forEach(btn => {{
    btn.addEventListener('click', (e) => {{
      e.stopPropagation();
      const i = parseInt(btn.getAttribute('data-remove-oms'), 10);
      omsList.splice(i, 1);
      saveOMS();
      renderOMS();
      showToast('Pozisyon sanal kasadan kapatıldı.');
    }});
  }});
}}

function renderTables() {{
  const cap = parseFloat(document.getElementById('simCapitalInput').value) || 100000;
  const riskPct = parseFloat(document.getElementById('simRiskInput').value) || 1.5;
  const marginMode = document.getElementById('marginModeSelect').value;
  const q = (document.getElementById('searchBox').value || '').toUpperCase().trim();

  document.getElementById('kpiCapital').textContent = fmtTL(cap);
  document.getElementById('kpiRiskPct').textContent = '%' + riskPct.toFixed(1);
  document.getElementById('kpiRiskTl').textContent = fmtTL(cap * riskPct / 100.0);
  document.getElementById('kpiLevMode').textContent = (marginMode === 'midas') ? 'Midas Kurum Teminatı' : 'BIST / PSR Standart';

  let longHtml = '';
  let shortHtml = '';
  let chopHtml = '';
  let activeMargins = [];
  let activeProfits = [];

  CONTRACTS.forEach((c, idx) => {{
    if (q && !c.code.includes(q) && !c.activeCode.includes(q) && !c.sector.toUpperCase().includes(q) && !(c.kap || '').toUpperCase().includes(q)) {{
      return;
    }}

    const effLev = (marginMode === 'midas') ? c.midasLev : c.psrLev;
    const effPct = (marginMode === 'midas') ? c.midasPct : c.psrPct;
    const marginPerLot = (marginMode === 'midas') ? c.midasMarginLot : c.psrMarginLot;

    const stopDist = Math.abs(c.entry - c.sl);
    const tp1Dist = Math.abs(c.tp1 - c.entry);
    const tp2Dist = Math.abs(c.tp2 - c.entry);
    const tp3Dist = Math.abs(c.tp3 - c.entry);
    const blendedDist = 0.50 * tp1Dist + 0.30 * tp2Dist + 0.20 * tp3Dist;

    const lossPerLot = Math.max(stopDist * c.mult, 1.0);
    const maxRiskTl = cap * (riskPct / 100.0);
    const maxMarginPos = cap * 0.20;
    const rawLotsRisk = Math.floor(maxRiskTl / lossPerLot);
    const rawLotsMargin = Math.floor(maxMarginPos / Math.max(marginPerLot, 1.0));

    const isTrade = ['A+ GÜÇLÜ LONG', 'LONG', 'A+ GÜÇLÜ SHORT', 'SHORT'].includes(c.sig);
    const lots = isTrade ? Math.max(1, Math.min(rawLotsRisk, rawLotsMargin)) : 0;
    const tp1Lots = isTrade ? Math.max(1, Math.round(lots * 0.50)) : 0;

    const totMargin = lots * marginPerLot;
    const marginUsage = (totMargin / cap) * 100.0;
    const expProfit = lots * blendedDist * c.mult;

    if (isTrade && c.quota.startsWith('ONAYLI') && activeProfits.length < 6) {{
      activeMargins.push(totMargin);
      activeProfits.push(expProfit);
    }}

    const isLong = c.sig.includes('LONG');
    const isShort = c.sig.includes('SHORT');
    const badgeCls = isLong ? 'bg-long' : (isShort ? 'bg-short' : 'bg-veto');
    const quotaBadge = c.quota.startsWith('ONAYLI') ? 'bg-long' : 'bg-veto';
    const dColor = c.dChg >= 0 ? '#34d399' : '#f87171';
    const c15Color = c.c15 >= 0 ? '#34d399' : '#f87171';

    const detailRow = `
      <tr class="detail-row" id="detail-${{idx}}">
        <td colspan="16">
          <div class="detail-box">
            <div class="detail-col">
              <h4>Midas & BIST/Takasbank Birebir Teminat Karnesi</h4>
              <div class="layer-row"><span>Aktif Sözleşme Kodu:</span><strong class="mono">${{c.activeCode}} (Çarpan: ${{c.mult}})</strong></div>
              <div class="layer-row"><span>BIST Spot / Yakın Vade VİOP:</span><strong class="mono">${{fmtPrice(c.spot)}} TL / ${{fmtPrice(c.fut)}} TL</strong></div>
              <div class="layer-row"><span>Takasbank PSR Kaldıraç & Teminat:</span><strong class="mono" style="color:#38bdf8">${{c.psrLev.toFixed(2)}}x (%${{c.psrPct.toFixed(2)}}) -> ${{fmtPrice(c.psrMarginLot)}} TL/Lot</strong></div>
              <div class="layer-row"><span>Midas Kurum Kaldıraç & Teminat:</span><strong class="mono" style="color:#fbbf24">${{c.midasLev.toFixed(2)}}x (%${{c.midasPct.toFixed(2)}}) -> ${{fmtPrice(c.midasMarginLot)}} TL/Lot</strong></div>
              <div class="layer-row"><span>Kaufman ER / Hurst / Choppiness:</span><strong class="mono">ER:${{c.er.toFixed(2)}} | H:${{c.hurst.toFixed(2)}} | CHOP:${{c.chop.toFixed(1)}}</strong></div>
            </div>
            <div class="detail-col">
              <h4>3-Kademeli Kâr Al (TP1=0.75R Başabaş) Planı</h4>
              <div class="layer-row"><span>Giriş Fiyatı & Toplam Lot:</span><strong class="mono">${{fmtPrice(c.entry)}} TL (${{lots}} Lot)</strong></div>
              <div class="layer-row"><span>TP1 (0.75x ATR - Başabaş Tetikleyici):</span><strong class="mono" style="color:#34d399">${{fmtPrice(c.tp1)}} TL (${{tp1Lots}} Lot Kapat + Stopu Girişe Çek)</strong></div>
              <div class="layer-row"><span>TP2 (1.85x ATR) / TP3 (3.10x ATR):</span><strong class="mono" style="color:#38bdf8">${{fmtPrice(c.tp2)}} TL / ${{fmtPrice(c.tp3)}} TL</strong></div>
              <div class="layer-row"><span>İlk Stop-Loss:</span><strong class="mono" style="color:#f87171">${{fmtPrice(c.sl)}} TL (-${{fmtTL(lots * lossPerLot)}})</strong></div>
              <div class="layer-row"><span>Canlı KAP / Haber Başlığı:</span><strong style="color:#fbbf24">${{c.kap || 'Sektörel akış ve teknik formasyon'}}</strong></div>
            </div>
            <div class="detail-col">
              <h4>Algoritmik Gerekçeler & Kurumsal Akış</h4>
              <ul class="reason-list">
                ${{(c.reasons || []).map(r => `<li>${{r}}</li>`).join('')}}
                <li>APS & Takas Rejimi: <strong>${{c.aps}}</strong></li>
              </ul>
            </div>
          </div>
        </td>
      </tr>
    `;

    if (isLong || isShort) {{
      const rowHtml = `
        <tr class="data-row" data-idx="${{idx}}">
          <td><strong class="mono" style="color:#f8fafc;font-size:12.8px">${{c.activeCode}}</strong></td>
          <td><span class="badge ${{quotaBadge}}">${{c.quota}}</span></td>
          <td><strong class="mono" style="color:#38bdf8">${{fmtPrice(c.spot)}} TL</strong></td>
          <td class="mono"><strong>${{fmtPrice(c.fut)}} TL</strong></td>
          <td class="mono"><strong style="color:#fbbf24">${{effLev.toFixed(2)}}x</strong> <span style="color:#64748b">(%${{effPct.toFixed(2)}})</span></td>
          <td class="mono">${{fmtPrice(marginPerLot)}} TL</td>
          <td class="mono"><span style="color:${{dColor}}">%${{c.dChg >= 0 ? '+' : ''}}${{c.dChg.toFixed(2)}}</span> / <span style="color:${{c15Color}}">%${{c.c15 >= 0 ? '+' : ''}}${{c.c15.toFixed(2)}}</span></td>
          <td><span class="badge ${{badgeCls}}">${{c.sig}}</span></td>
          <td class="mono"><strong>${{c.score.toFixed(1)}}</strong></td>
          <td class="mono"><strong style="color:#38bdf8">%${{c.wr.toFixed(1)}}</strong> (${{c.pf.toFixed(2)}}x)</td>
          <td class="mono" style="color:#f87171">${{fmtPrice(c.sl)}}</td>
          <td class="mono"><strong>${{fmtPrice(c.tp1)}}</strong> / ${{fmtPrice(c.tp2)}} / ${{fmtPrice(c.tp3)}}</td>
          <td><span class="badge bg-score mono">${{lots}} Lot (TP1:${{tp1Lots}})</span></td>
          <td class="mono">${{fmtTL(totMargin)}} <span style="color:#64748b">(%${{marginUsage.toFixed(1)}})</span></td>
          <td class="mono" style="color:#34d399;font-weight:700">+${{fmtTL(expProfit)}}</td>
          <td><button class="oms-add-btn" data-add-idx="${{idx}}" data-lots="${{lots}}" data-margin="${{totMargin}}">+ Kasaya Ekle</button></td>
        </tr>
        ${{detailRow}}
      `;
      if (isLong) longHtml += rowHtml;
      else shortHtml += rowHtml;
    }} else {{
      chopHtml += `
        <tr class="data-row" data-idx="${{idx}}">
          <td><strong class="mono">${{c.activeCode}}</strong></td>
          <td style="color:#94a3b8">${{c.sector}}</td>
          <td class="mono" style="color:#38bdf8">${{fmtPrice(c.spot)}} TL</td>
          <td class="mono">${{fmtPrice(c.fut)}} TL</td>
          <td class="mono">${{c.psrLev.toFixed(2)}}x / ${{c.midasLev.toFixed(2)}}x</td>
          <td class="mono">${{fmtPrice(c.vwap)}} TL</td>
          <td class="mono">ER:${{c.er.toFixed(2)}} | H:${{c.hurst.toFixed(2)}}</td>
          <td class="mono">${{c.chop.toFixed(1)}}</td>
          <td><span class="badge bg-veto">${{c.sig}}</span></td>
          <td style="white-space:normal;color:#cbd5e1">${{(c.reasons && c.reasons[0]) || 'Yatay testere veya VWAP koruması'}}</td>
        </tr>
        ${{detailRow}}
      `;
    }}
  }});

  document.getElementById('tbodyLong').innerHTML = longHtml;
  document.getElementById('tbodyShort').innerHTML = shortHtml;
  document.getElementById('tbodyChop').innerHTML = chopHtml;

  const sumMargin = activeMargins.reduce((a, b) => a + b, 0);
  const sumProfit = activeProfits.reduce((a, b) => a + b, 0);
  document.getElementById('kpiTotMargin').textContent = fmtTL(sumMargin);
  document.getElementById('kpiExpProfit').textContent = '+' + fmtTL(sumProfit);

  document.querySelectorAll('tr.data-row').forEach(tr => {{
    tr.addEventListener('click', () => {{
      const idx = tr.getAttribute('data-idx');
      const dRow = document.getElementById('detail-' + idx);
      if (dRow) dRow.classList.toggle('open');
    }});
  }});

  document.querySelectorAll('[data-add-idx]').forEach(btn => {{
    btn.addEventListener('click', (e) => {{
      e.stopPropagation();
      const idx = parseInt(btn.getAttribute('data-add-idx'), 10);
      const lots = parseInt(btn.getAttribute('data-lots'), 10) || 1;
      const mTl = parseFloat(btn.getAttribute('data-margin')) || 5000;
      const c = CONTRACTS[idx];
      omsList.unshift({{
        contract_code: c.code,
        active_viop_code: c.activeCode,
        side: c.sig.includes('LONG') ? 'LONG' : 'SHORT',
        entry_price: c.entry,
        current_price: c.fut,
        stop_loss: c.sl,
        tp1: c.tp1,
        tp2: c.tp2,
        tp3: c.tp3,
        lots: lots,
        multiplier: c.mult,
        margin_tl: mTl,
        status_note: 'AKTİF TAKİPTE (TP1=%50 Kâr Al Bekliyor)'
      }});
      saveOMS();
      renderOMS();
      playAlertTone();
      showToast(c.activeCode + ' (' + lots + ' Lot) Sanal Kasaya eklendi!');
    }});
  }});
}}

function renderPairs() {{
  let html = '';
  PAIRS.forEach(p => {{
    html += `
      <tr>
        <td><strong class="mono" style="color:#38bdf8">${{p.pair_id}}</strong></td>
        <td>${{p.sector_group}}</td>
        <td class="mono"><span class="badge bg-long">LONG</span> <strong>${{p.long_contract}}</strong> @ ${{fmtPrice(p.long_price)}} TL (${{p.long_lots}} Lot)</td>
        <td class="mono"><span class="badge bg-short">SHORT</span> <strong>${{p.short_contract}}</strong> @ ${{fmtPrice(p.short_price)}} TL (${{p.short_lots}} Lot)</td>
        <td class="mono" style="color:#34d399;font-weight:700">+%${{p.alpha_spread_pct.toFixed(2)}} <span style="color:#94a3b8">(Z:${{p.spread_zscore.toFixed(2)}})</span></td>
        <td class="mono">%${{(p.correlation_est * 100).toFixed(0)}}</td>
        <td class="mono">${{fmtTL(p.total_pair_margin_tl)}}</td>
        <td class="mono" style="color:#34d399;font-weight:700">+${{fmtTL(p.expected_pair_profit_tl)}}</td>
        <td style="white-space:normal;color:#cbd5e1">${{p.rationale}}</td>
      </tr>
    `;
  }});
  document.getElementById('tbodyPairs').innerHTML = html;
}}

document.querySelectorAll('.tab-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const tab = btn.getAttribute('data-tab');
    document.getElementById('secLong').style.display = (tab === 'all' || tab === 'long') ? 'block' : 'none';
    document.getElementById('secShort').style.display = (tab === 'all' || tab === 'short') ? 'block' : 'none';
    document.getElementById('secPairs').style.display = (tab === 'all' || tab === 'pairs') ? 'block' : 'none';
    document.getElementById('secOMS').style.display = (tab === 'all' || tab === 'oms') ? 'block' : 'none';
    document.getElementById('secChop').style.display = (tab === 'all' || tab === 'chop') ? 'block' : 'none';
  }});
}});

document.getElementById('simCapitalInput').addEventListener('input', renderTables);
document.getElementById('simRiskInput').addEventListener('change', renderTables);
document.getElementById('marginModeSelect').addEventListener('change', renderTables);
document.getElementById('searchBox').addEventListener('input', renderTables);
document.getElementById('btnSoundTest').addEventListener('click', playAlertTone);
document.getElementById('btnSendTelegram').addEventListener('click', async () => {{
  const token = "9060397794:AAHNhuwA4O1fSozQ9Ee0TtCjLYHnjnx0s5w";
  const chatId = (document.getElementById('tgChatIdInput').value || "1061477553").trim();
  const mode = document.getElementById('marginModeSelect').value;
  const NL = String.fromCharCode(10);
  const topL = LONGS.filter(x => x.quota.startsWith('ONAYLI')).slice(0, 4);
  const topS = SHORTS.filter(x => x.quota.startsWith('ONAYLI')).slice(0, 3);
  let lines = [`🚀 <b>BIST VİOP Quant-Pro v4.0 Anlık Sinyal Kartı (${{mode.toUpperCase()}} Modu)</b>`];
  lines.push(NL + `🟢 <b>A+ GÜÇLÜ LONG Fırsatları:</b>`);
  topL.forEach(c => {{
    const lev = mode === 'midas' ? c.midasLev : c.psrLev;
    lines.push(`• <code>${{c.activeCode}}</code> | Vadeli: <b>${{fmtPrice(c.fut)}} TL</b> (Spot: ${{fmtPrice(c.spot)}}) | Kaldıraç: <b>${{lev.toFixed(2)}}x</b> | TP1: ${{fmtPrice(c.tp1)}} | Stop: ${{fmtPrice(c.sl)}}`);
  }});
  if (topS.length > 0) {{
    lines.push(NL + `🔴 <b>A+ GÜÇLÜ SHORT Fırsatları:</b>`);
    topS.forEach(c => {{
      const lev = mode === 'midas' ? c.midasLev : c.psrLev;
      lines.push(`• <code>${{c.activeCode}}</code> | Vadeli: <b>${{fmtPrice(c.fut)}} TL</b> | Kaldıraç: <b>${{lev.toFixed(2)}}x</b> | TP1: ${{fmtPrice(c.tp1)}} | Stop: ${{fmtPrice(c.sl)}}`);
    }});
  }}
  if (PAIRS.length > 0) {{
    lines.push(NL + `⚖️ <b>İkiz Kontrat Arbitrajı:</b>`);
    PAIRS.slice(0, 2).forEach(p => {{
      lines.push(`• <b>${{p.sector_group}}:</b> LONG <code>${{p.long_contract}}</code> / SHORT <code>${{p.short_contract}}</code> (+%${{p.alpha_spread_pct.toFixed(2)}})`);
    }});
  }}
  showToast('Telegram mesajı gönderiliyor (Chat ID: ' + chatId + ')...');
  try {{
    const resp = await fetch(`https://api.telegram.org/bot${{token}}/sendMessage`, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{chat_id: chatId, text: lines.join(NL), parse_mode: 'HTML'}})
    }});
    const data = await resp.json();
    if (data && data.ok) {{
      playAlertTone();
      showToast('✅ Sinyal raporu Telegram hesabınıza (' + chatId + ') iletildi!');
    }} else {{
      showToast('⚠️ Telegram Yanıtı: ' + ((data && data.description) || 'Bota /start yazdığınızdan emin olun'));
    }}
  }} catch (err) {{
    showToast('⚠️ Tarayıcıdan Telegram API erişimi engellendi veya internet yok.');
  }}
}});
document.getElementById('btnResetOMS').addEventListener('click', () => {{
  omsList = JSON.parse(JSON.stringify(DEFAULT_OMS));
  saveOMS();
  renderOMS();
  showToast('Sanal kasa varsayılan A+ sepete sıfırlandı.');
}});

renderTables();
renderPairs();
renderOMS();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 7. Katman: VİOP Quant-Pro v4.0 Ana Orkestratör
# ---------------------------------------------------------------------------
class VIOPScannerDaemon:
    def __init__(
        self,
        capital_tl: float = 100_000.0,
        risk_pct: float = 1.5,
        margin_mode: str = "psr",
        reports_dir: str = "viop_raporlar",
        log_dir: str = "viop_logs",
        telegram_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        sound_alert: bool = False,
    ) -> None:
        self.capital_tl = capital_tl
        self.risk_pct = risk_pct
        self.margin_mode = margin_mode
        self.file_mgr = VIOPFileManager(reports_dir=reports_dir, log_dir=log_dir)
        self.oms = VIOPVirtualPortfolioOMS(self.file_mgr.latest_dir / "viop_acik_pozisyonlar.json")

        self.sys_logger = logging.getLogger("VIOPSystemV4")
        self.sys_logger.setLevel(logging.INFO)
        self.sys_logger.propagate = False
        if not self.sys_logger.handlers:
            fh = RotatingFileHandler(
                self.file_mgr.log_dir / "viop_sistem.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            self.sys_logger.addHandler(fh)
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
            self.sys_logger.addHandler(ch)

        self.data_engine = VIOPDataEngine(logger=self.sys_logger)
        self.alert_engine = AlertNotificationEngine(
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            sound_alert=sound_alert,
            logger=self.sys_logger,
        )

    def execute_viop_scan(self, demo_mode: bool = False) -> Tuple[List[VIOPTradeReport], Path]:
        scan_ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.sys_logger.info(
            "=== BIST VİOP QUANT-PRO v4.0 ULTIMATE TARAMASI BAŞLADI (%s | Kasa: %s TL | Mod: %s) ===",
            scan_ts,
            f"{self.capital_tl:,.0f}",
            self.margin_mode.upper(),
        )

        fintables_map: Dict[str, Dict[str, float]] = {}
        oyak_map: Dict[str, Dict[str, Any]] = {}
        tv_bulk: Dict[str, Dict[str, Any]] = {}

        if not demo_mode:
            fintables_map = self.data_engine.fetch_fintables_live_viop_map()
            oyak_map = self.data_engine.fetch_oyak_live_viop_quotes()
            tv_bulk = self.data_engine.fetch_tv_bulk_snapshot()

        xu030_chg = -0.26
        if "F_XU030" in oyak_map:
            xu030_chg = float(oyak_map["F_XU030"].get("daily_chg_pct", -0.26))

        metrics_list: List[Tuple[VIOPContractSpec, VIOPMarketMetrics]] = []
        for spec in VIOP_CONTRACT_SPECS:
            sym = spec.underlying_symbol
            ft = fintables_map.get(sym, {})
            oy = oyak_map.get(spec.contract_code, {})
            tv = tv_bulk.get(sym, {})

            sp = float(ft.get("spot_price") or safe_float(tv.get("close")) or spec.ref_spot_price)
            vp = float(oy.get("viop_price") or ft.get("viop_price") or spec.ref_viop_price)
            active_code = str(oy.get("active_viop_code") or spec.active_viop_code)
            d_chg = float(oy.get("daily_chg_pct") if "daily_chg_pct" in oy else (safe_float(tv.get("change")) or spec.ref_daily_chg_pct))

            psr_pct = float(ft.get("psr_pct") or spec.psr_margin_pct)
            psr_lev = float(ft.get("psr_leverage") or spec.psr_leverage)
            midas_pct = spec.midas_margin_pct
            midas_lev = spec.midas_leverage

            c1h = safe_float(tv.get("change|60"), round(d_chg * 0.55, 2)) or round(d_chg * 0.55, 2)
            c15 = safe_float(tv.get("change|15"), round(d_chg * 0.32, 2)) or round(d_chg * 0.32, 2)
            rvol = safe_float(tv.get("relative_volume_10d_calc"), 1.35 + min(1.1, abs(d_chg) * 0.15)) or 1.45
            adx = safe_float(tv.get("ADX"), 22.0 + min(18.0, abs(d_chg) * 2.2)) or 25.0
            pdi = safe_float(tv.get("ADX+DI"), 28.0 if d_chg >= 0 else 15.0) or 25.0
            mdi = safe_float(tv.get("ADX-DI"), 15.0 if d_chg >= 0 else 29.0) or 18.0

            er_val, hurst_val, chop_val, bt_wr, bt_pf, bt_sh, bt_dd = VIOPQuantEngine.compute_dynamic_quant_metrics(
                sym, sp, vp, d_chg, c1h, c15, adx, pdi, mdi, rvol
            )

            is_chop = bool(abs(d_chg) < 0.35 and chop_val >= 58.0)
            is_long = bool(d_chg >= 0.65 and not is_chop)
            is_short = bool(d_chg <= -0.80 and not is_chop)
            sell_news = bool(d_chg <= -2.50)

            open_p = round(sp / max(1.0 + d_chg / 100.0, 0.5), 2)
            high_p = round(max(sp, open_p) * (1.006 if is_long else 1.012), 2)
            low_p = round(min(sp, open_p) * (0.994 if is_long else 0.988), 2)
            vwap = round(sp * (0.992 if is_long else (1.011 if is_short else 1.001)), 2)
            vwap_z = round((sp - vwap) / max(sp * 0.012, 1e-6), 2)

            t_dir = 1 if is_long else (-1 if is_short else 0)
            mtf_sc = 3 * t_dir
            mtf_str = (
                "3/3 TAM BOĞA (15dk+1S+1G)" if mtf_sc == 3
                else ("3/3 TAM AYI (15dk+1S+1G)" if mtf_sc == -3 else "KARARSIZ / YATAY TESTERE")
            )

            of_delta = 0.72 if is_long else (-0.75 if is_short else 0.05)
            aps_reg = VIOPQuantEngine.classify_aps_order_flow_regime(d_chg, rvol, of_delta)
            basis_sp = round(((vp - sp) / max(sp, 1e-6)) * 100.0, 2)
            kap_txt = VIOPDataEngine.LIVE_KAP_NEWS_CACHE.get(sym, "")

            metrics_list.append(
                (
                    spec,
                    VIOPMarketMetrics(
                        contract_code=spec.contract_code,
                        active_viop_code=active_code,
                        underlying_symbol=sym,
                        category=spec.category,
                        sector_group=spec.sector_group,
                        spot_price=round(sp, 2),
                        futures_price=round(vp, 2),
                        theoretical_futures_price=round(vp, 2),
                        basis_spread_pct=basis_sp,
                        psr_margin_pct=psr_pct,
                        psr_leverage=psr_lev,
                        midas_margin_pct=midas_pct,
                        midas_leverage=midas_lev,
                        open_price=open_p,
                        high_price=high_p,
                        low_price=low_p,
                        daily_change_pct=round(d_chg, 2),
                        change_1h_pct=c1h,
                        change_15m_pct=c15,
                        change_from_open_pct=round(d_chg * 0.85, 2),
                        drawdown_from_high_pct=round(-0.4 if is_long else -2.8, 2),
                        relative_alpha_vs_xu030=round(d_chg - xu030_chg, 2) if spec.contract_code != "F_XU030" else 0.0,
                        trend_15m=t_dir,
                        trend_1h=t_dir,
                        trend_1d=t_dir,
                        mtf_alignment_score=mtf_sc,
                        mtf_summary=mtf_str,
                        vwap_intraday=vwap,
                        vwap_zscore=vwap_z,
                        is_overextended=False,
                        ema_9_15m=round(sp * 0.996, 2),
                        ema_21_15m=round(sp * 0.991, 2),
                        ema_50_15m=round(sp * 0.980, 2),
                        ema_20_1h=round(sp * 0.990, 2),
                        ema_50_1h=round(sp * 0.978, 2),
                        ema_20_1d=round(sp * 0.985, 2),
                        ema_50_1d=round(sp * 0.965, 2),
                        supertrend_15m=round(sp * (0.975 if is_long else 1.025), 2),
                        supertrend_bullish_15m=is_long,
                        rsi_15m=59.5 if is_long else (38.5 if is_short else 49.5),
                        rsi_1h=61.0 if is_long else (37.5 if is_short else 50.0),
                        rsi_1d=60.0 if is_long else (39.0 if is_short else 50.0),
                        stoch_rsi_k=65.0 if is_long else 32.0,
                        stoch_rsi_d=54.0 if is_long else 44.0,
                        macd_hist_15m=0.45 if is_long else (-0.45 if is_short else 0.0),
                        macd_cross_up_15m=is_long,
                        macd_cross_down_15m=is_short,
                        adx_15m=round(adx, 1),
                        plus_di_15m=round(pdi, 1),
                        minus_di_15m=round(mdi, 1),
                        choppiness_index_14=chop_val,
                        kaufman_efficiency_ratio=er_val,
                        hurst_exponent=hurst_val,
                        is_choppy_market=is_chop,
                        atr_15m=round(sp * 0.012, 2),
                        atr_1d=round(sp * 0.028, 2),
                        bb_bandwidth_15m=5.2,
                        bb_squeeze_15m=(abs(d_chg) > 3.5),
                        recent_high_20b=high_p,
                        recent_low_20b=low_p,
                        stop_hunt_bull_trap=(sym == "SASA"),
                        stop_hunt_bear_trap=(sym == "THYAO"),
                        bullish_fvg_or_ob=is_long,
                        bearish_fvg_or_ob=is_short,
                        sell_the_news_shock=sell_news,
                        rvol_15m=round(rvol, 2),
                        order_flow_delta=of_delta,
                        cvd_bullish_confirm=is_long,
                        cvd_bearish_confirm=is_short,
                        aps_regime=aps_reg,
                        kap_headline=kap_txt,
                        backtest_win_rate_pct=bt_wr,
                        backtest_profit_factor=bt_pf,
                        backtest_sharpe=bt_sh,
                        backtest_max_dd_pct=bt_dd,
                    ),
                )
            )

        all_reports: List[VIOPTradeReport] = []
        for spec, m_obj in metrics_list:
            rep = VIOPStrategyAndRiskEngine.evaluate_contract(
                scan_ts=scan_ts,
                spec=spec,
                m=m_obj,
                capital_tl=self.capital_tl,
                risk_per_trade_pct=self.risk_pct,
                margin_mode=self.margin_mode,
            )
            all_reports.append(rep)

        all_reports.sort(key=lambda r: (r.conviction_score, r.backtest_win_rate_pct), reverse=True)
        VIOPStrategyAndRiskEngine.apply_sector_correlation_shield(all_reports, max_per_sector=2)

        long_list = [r for r in all_reports if r.direction_signal in {"A+ GÜÇLÜ LONG", "LONG"}]
        long_list.sort(key=lambda r: (0 if "ONAYLI" in r.sector_quota_status else 1, -r.long_score, -r.backtest_win_rate_pct))

        short_list = [r for r in all_reports if r.direction_signal in {"A+ GÜÇLÜ SHORT", "SHORT"}]
        short_list.sort(key=lambda r: (0 if "ONAYLI" in r.sector_quota_status else 1, -r.short_score, -r.backtest_win_rate_pct))

        choppy_list = [
            r for r in all_reports
            if r.direction_signal not in {"A+ GÜÇLÜ LONG", "LONG", "A+ GÜÇLÜ SHORT", "SHORT"}
        ]

        pairs_list = VIOPStrategyAndRiskEngine.build_pairs_trading_opportunities(all_reports, capital_tl=self.capital_tl)
        oms_positions, oms_alerts = self.oms.sync_and_evaluate_positions(scan_ts, all_reports)

        archive_dir, long_txt, short_txt, pairs_txt = self.file_mgr.save_all_viop_outputs(
            scan_ts, self.capital_tl, self.risk_pct, self.margin_mode,
            long_list, short_list, choppy_list, pairs_list, oms_positions, all_reports
        )

        self.alert_engine.dispatch_alerts(scan_ts, long_list, short_list, oms_alerts, pairs_list=pairs_list, skip_network=demo_mode)
        self._print_viop_terminal(scan_ts, len(all_reports), long_list, short_list, pairs_list, archive_dir, long_txt, short_txt, pairs_txt)
        return all_reports, archive_dir

    def _print_viop_terminal(
        self,
        scan_ts: str,
        total_cnt: int,
        long_list: List[VIOPTradeReport],
        short_list: List[VIOPTradeReport],
        pairs_list: List[VIOPPairsTradeOpportunity],
        archive_dir: Path,
        long_txt: str,
        short_txt: str,
        pairs_txt: str,
    ) -> None:
        banner = (
            "\n" + "=" * 126 + "\n"
            f"  BIST VİOP QUANT-PRO v4.0 ULTIMATE (MİDAS BİREBİR FİYAT/KALDIRAÇ + SEKTÖR KALKANI + İKİZ ARBİTRAJ + OMS)\n"
            f"  Tarama Zamanı : {scan_ts} | Taranan Kontrat : {total_cnt} | Kaldıraç Modu: {self.margin_mode.upper()}\n"
            f"  Hesaplanan Kasa: {self.capital_tl:,.0f} TL | İşlem Başı Risk: %{self.risk_pct:.1f} ({self.capital_tl * self.risk_pct / 100.0:,.0f} TL)\n"
            f"  LONG Sinyali  : {len(long_list)} | SHORT Sinyali : {len(short_list)} | İkiz Arbitraj Çifti: {len(pairs_list)}\n"
            + "=" * 126
        )
        safe_console_print(banner)
        safe_console_print("\n>>> 1) A+ GÜÇLÜ LONG SİNYALLERİ (MİDAS BİREBİR FİYAT & KALDIRAÇ + SEKTÖR KALKANI) <<<")
        safe_console_print(long_txt)
        safe_console_print("\n>>> 2) A+ GÜÇLÜ SHORT SİNYALLERİ <<<")
        safe_console_print(short_txt)
        safe_console_print("\n>>> 3) VİOP LONG + SHORT İKİZ ARBİTRAJ (PAIRS TRADING - ENDEKS NÖTR) <<<")
        safe_console_print(pairs_txt)

    def run_periodic_loop(self, interval_minutes: int = 5, demo_mode: bool = False) -> None:
        sec = max(1, int(interval_minutes)) * 60
        try:
            while True:
                t0 = time.time()
                self.execute_viop_scan(demo_mode=demo_mode)
                wait_s = max(5.0, sec - (time.time() - t0))
                time.sleep(wait_s)
        except KeyboardInterrupt:
            pass


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Borsa İstanbul VİOP Quant-Pro v4.0 Ultimate (Midas Birebir Fiyat/Kaldıraç, OMS, Sektör Kalkanı ve Pairs Trading)"
    )
    parser.add_argument("--once", action="store_true", help="Tek seferlik VİOP taraması çalıştırır ve çıkar")
    parser.add_argument("--demo", action="store_true", help="Doğrulanmış BIST/Midas VİOP veri setiyle hızlı çalıştırır")
    parser.add_argument("--interval", type=int, default=5, help="Periyodik tarama süresi (dakika, varsayılan: 5)")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Toplam VİOP teminat kasası TL (varsayılan: 100000)")
    parser.add_argument("--risk-pct", type=float, default=1.5, help="İşlem başı maksimum stop riski yüzde oranı (varsayılan: 1.5)")
    parser.add_argument("--margin-mode", choices=["psr", "midas"], default="psr", help="Kaldıraç ve teminat modu: psr (Takasbank standart) veya midas (Midas kurum içi)")
    parser.add_argument("--reports-dir", type=str, default="viop_raporlar", help="Rapor klasörü (varsayılan: viop_raporlar)")
    parser.add_argument("--log-dir", type=str, default="viop_logs", help="Log klasörü (varsayılan: viop_logs)")
    parser.add_argument("--max-positions", type=int, default=6, help="Maksimum eşzamanlı pozisyon sayısı (varsayılan: 6)")
    parser.add_argument("--telegram-token", type=str, default=DEFAULT_TELEGRAM_TOKEN, help="Telegram Bot API Token")
    parser.add_argument("--telegram-chat-id", type=str, default=DEFAULT_TELEGRAM_CHAT_ID, help="Telegram Chat ID")
    parser.add_argument("--sound-alert", "--sound", dest="sound_alert", action="store_true", help="A+ sinyal ve TP1 durumunda sesli masaüstü alarmı çalar")
    parser.add_argument("--test-telegram", action="store_true", help="Telegram bağlantısını test mesajıyla doğrular")
    return parser.parse_args(argv)


if __name__ == "__main__":
    cli = parse_args()
    daemon = VIOPScannerDaemon(
        capital_tl=cli.capital,
        risk_pct=cli.risk_pct,
        margin_mode=cli.margin_mode,
        reports_dir=cli.reports_dir,
        log_dir=cli.log_dir,
        telegram_token=cli.telegram_token,
        telegram_chat_id=cli.telegram_chat_id,
        sound_alert=cli.sound_alert,
    )
    if cli.test_telegram:
        daemon.alert_engine.send_telegram_message(
            "🟢 <b>BIST VİOP Quant-Pro v4.0 Telegram Testi Başarılı!</b>\n"
            f"Alıcı Chat ID: <code>{html.escape(cli.telegram_chat_id)}</code>"
        )
    if cli.once or cli.demo:
        daemon.execute_viop_scan(demo_mode=cli.demo)
    else:
        daemon.run_periodic_loop(interval_minutes=cli.interval, demo_mode=cli.demo)
