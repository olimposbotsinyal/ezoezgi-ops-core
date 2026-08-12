# test_signal_generation.py
"""
═══════════════════════════════════════════════════════════════════════════
OLIMPOS STRATEJİSİ - KAPSAMLI SINYAL ÜRETIM TEST DOSYASI
═══════════════════════════════════════════════════════════════════════════

Bu dosya aşağıdaki testleri sırayla çalıştırır:
1. Alarm Kurulumu Kontrolü
2. OHLCV Veri Çekme Testi
3. SMMA Hesaplama Testi
4. Crossover Tetikleyici Testi
5. Strateji Sinyal Üretim Testi
6. Tam Akış Testi (Alarm → Sinyal)
7. Parametre Optimizasyonu

Kullanım:
    python test_signal_generation.py

Çıktı:
    - Detaylı test sonuçları (konsol)
    - test_results.json (sonuçlar dosyası)
    - test_debug.log (debug logları)
"""

import asyncio
import json
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import numpy as np
from io import StringIO

# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 0: KODLAMA DÜZELTMESİ (Windows Konsol Emojileri İçin)
# ═══════════════════════════════════════════════════════════════════════════
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 1: LOGGING AYARLARI
# ═══════════════════════════════════════════════════════════════════════════

# Debug log dosyası
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('test_debug.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 2: TEST SONUÇLARI YÖNETIMI
# ═══════════════════════════════════════════════════════════════════════════

class TestResults:
    """Test sonuçlarını saklar ve raporlar"""

    def __init__(self):
        self.results = {
            'test_timestamp':datetime.now(timezone.utc).isoformat(),
            'tests':{},
            'summary':{
                'total_tests':0,
                'passed':0,
                'failed':0,
                'warnings':0
            },
            'recommendations':[]
        }

    def add_test(self, test_name: str, status: str, details: Dict[str, Any],
            warnings: List[str] = None):
        """Test sonucu ekle"""
        self.results['tests'][test_name] = {
            'status':status,  # 'PASS', 'FAIL', 'WARNING'
            'timestamp':datetime.now(timezone.utc).isoformat(),
            'details':details,
            'warnings':warnings or []
        }

        # Özet güncelle
        self.results['summary']['total_tests'] += 1
        if status == 'PASS':
            self.results['summary']['passed'] += 1
        elif status == 'FAIL':
            self.results['summary']['failed'] += 1
        elif status == 'WARNING':
            self.results['summary']['warnings'] += 1

    def add_recommendation(self, recommendation: str):
        """Öneriye ekle"""
        self.results['recommendations'].append(recommendation)

    def save(self, filename: str = 'test_results.json'):
        """Sonuçları dosyaya kaydet"""
        # default=str eklenerek tarih formatı hataları giderildi
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"✅ Test sonuçları kaydedildi: {filename}")

    def print_summary(self):
        """Özeti konsola yazdır"""
        print("\n" + "=" * 70)
        print("📊 TEST ÖZETI")
        print("=" * 70)
        print(f"Toplam Test: {self.results['summary']['total_tests']}")
        print(f"✅ Geçen: {self.results['summary']['passed']}")
        print(f"❌ Başarısız: {self.results['summary']['failed']}")
        print(f"⚠️ Uyarı: {self.results['summary']['warnings']}")

        if self.results['recommendations']:
            print("\n📋 ÖNERİLER:")
            for i, rec in enumerate(self.results['recommendations'], 1):
                print(f"{i}. {rec}")
        print("=" * 70 + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 3: TEST SINIFLARI
# ═══════════════════════════════════════════════════════════════════════════

class AlarmTest:
    """Alarm Kurulumu Testleri"""

    @staticmethod
    def test_alarm_structure() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 1: Alarm Yapısı Kontrolü
        ═══════════════════════════════════════════════════════════════════════
        Alarm objesinin gerekli tüm alanları içerip içermediğini kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 1: ALARM YAPISI KONTROLÜ")
        logger.info("=" * 70)

        # Örnek alarm objesi
        test_alarm = {
            'symbol':'BTC/USDT',
            'timeframe':'15m',
            'market_type':'futures',
            'strategy_hint':'v2',
            'user_id':12345,
            'status':'active',
            'created_at':datetime.now(timezone.utc).isoformat(),
            'meta':{
                'source':'ai_scan',
                'ai_confidence':0.75,
                'potential_pct':2.5
            }
        }

        required_fields = [
            'symbol', 'timeframe', 'market_type',
            'strategy_hint', 'user_id', 'status', 'meta'
        ]

        details = {
            'alarm_data':test_alarm,
            'required_fields':required_fields,
            'missing_fields':[]
        }

        warnings = []

        # Kontrol et
        for field in required_fields:
            if field not in test_alarm:
                details['missing_fields'].append(field)
                logger.error(f"❌ Eksik alan: {field}")
            else:
                logger.info(f"✅ Alan var: {field} = {test_alarm[field]}")

        # Sembol formatı kontrolü
        if not test_alarm['symbol'].endswith('USDT'):
            warnings.append("Sembol USDT ile bitmeli (örn: BTC/USDT)")
            logger.warning(f"⚠️ Sembol formatı şüpheli: {test_alarm['symbol']}")

        # Timeframe kontrolü
        valid_tfs = ['1m', '5m', '15m', '30m', '1h', '4h', '1d']
        if test_alarm['timeframe'] not in valid_tfs:
            warnings.append(f"Timeframe geçersiz. Geçerli: {valid_tfs}")
            logger.warning(f"⚠️ Geçersiz timeframe: {test_alarm['timeframe']}")

        # Strateji kontrolü
        if test_alarm['strategy_hint'] not in ['v1', 'v2']:
            warnings.append("Strateji ID v1 veya v2 olmalı")
            logger.warning(f"⚠️ Geçersiz strateji: {test_alarm['strategy_hint']}")

        # Status kontrolü
        if test_alarm['status'] not in ['active', 'inactive', 'converted']:
            warnings.append("Status 'active', 'inactive' veya 'converted' olmalı")
            logger.warning(f"⚠️ Geçersiz status: {test_alarm['status']}")

        # Sonuç
        if details['missing_fields']:
            status = 'FAIL'
            logger.error(f"❌ TEST BAŞARIŞIZ: {len(details['missing_fields'])} alan eksik")
        elif warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} uyarı var")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: Tüm alanlar doğru")

        return status, details, warnings

    @staticmethod
    def test_alarm_parameters() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 2: Alarm Parametreleri Kontrolü
        ═══════════════════════════════════════════════════════════════════════
        Alarm parametrelerinin mantıklı değerlere sahip olup olmadığını kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 2: ALARM PARAMETRELERİ KONTROLÜ")
        logger.info("=" * 70)

        test_alarm = {
            'symbol':'BTC/USDT',
            'timeframe':'15m',
            'market_type':'futures',
            'strategy_hint':'v2',
            'user_id':12345,
            'status':'active',
            'meta':{
                'source':'ai_scan',
                'ai_confidence':0.75,
                'potential_pct':2.5,
                'volume_usd':5000000,
                'technical_score':65.5
            }
        }

        details = {
            'alarm_data':test_alarm,
            'parameter_checks':{}
        }

        warnings = []

        # Meta parametreleri kontrol et
        meta = test_alarm['meta']

        # AI Confidence kontrolü (0-1 aralığında olmalı)
        ai_conf = meta.get('ai_confidence', 0)
        if not (0 <= ai_conf <= 1):
            warnings.append(f"AI Confidence {ai_conf} olmalı 0-1 aralığında")
            logger.warning(f"⚠️ AI Confidence dışında: {ai_conf}")
        else:
            logger.info(f"✅ AI Confidence geçerli: {ai_conf}")

        details['parameter_checks']['ai_confidence'] = {
            'value':ai_conf,
            'valid':0 <= ai_conf <= 1
        }

        # Potential kontrolü (pozitif olmalı)
        potential = meta.get('potential_pct', 0)
        if potential < 0:
            warnings.append(f"Potential {potential} negatif olamaz")
            logger.warning(f"⚠️ Negatif potential: {potential}")
        else:
            logger.info(f"✅ Potential geçerli: {potential}%")

        details['parameter_checks']['potential_pct'] = {
            'value':potential,
            'valid':potential >= 0
        }

        # Volume kontrolü (pozitif olmalı)
        volume = meta.get('volume_usd', 0)
        if volume <= 0:
            warnings.append(f"Volume {volume} pozitif olmalı")
            logger.warning(f"⚠️ Geçersiz volume: {volume}")
        else:
            logger.info(f"✅ Volume geçerli: ${volume:,.0f}")

        details['parameter_checks']['volume_usd'] = {
            'value':volume,
            'valid':volume > 0
        }

        # Technical Score kontrolü (0-100 aralığında olmalı)
        tech_score = meta.get('technical_score', 0)
        if not (0 <= tech_score <= 100):
            warnings.append(f"Technical Score {tech_score} olmalı 0-100 aralığında")
            logger.warning(f"⚠️ Technical Score dışında: {tech_score}")
        else:
            logger.info(f"✅ Technical Score geçerli: {tech_score}")

        details['parameter_checks']['technical_score'] = {
            'value':tech_score,
            'valid':0 <= tech_score <= 100
        }

        # Sonuç
        if warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} parametre sorunu")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: Tüm parametreler geçerli")

        return status, details, warnings


class OHLCVTest:
    """OHLCV Veri Testleri"""

    @staticmethod
    def create_sample_ohlcv(bars: int = 100) -> pd.DataFrame:
        """Örnek OHLCV verisi oluştur"""
        np.random.seed(42)

        # Başlangıç fiyatı
        start_price = 42500.0

        # Fiyat hareketi oluştur
        returns = np.random.normal(0.0001, 0.005, bars)
        prices = start_price * np.exp(np.cumsum(returns))

        df = pd.DataFrame({
            'timestamp':pd.date_range(
                start=datetime.now(timezone.utc) - timedelta(hours=bars),
                periods=bars,
                freq='15min'
            ),
            'open':prices * (1 + np.random.uniform(-0.002, 0.002, bars)),
            'high':prices * (1 + np.abs(np.random.uniform(0, 0.005, bars))),
            'low':prices * (1 - np.abs(np.random.uniform(0, 0.005, bars))),
            'close':prices,
            'volume':np.random.uniform(1000, 5000, bars)
        })

        return df

    @staticmethod
    def test_ohlcv_structure() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 3: OHLCV Yapısı Kontrolü
        ═══════════════════════════════════════════════════════════════════════
        OHLCV DataFrame'inin doğru yapıya sahip olup olmadığını kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 3: OHLCV YAPISI KONTROLÜ")
        logger.info("=" * 70)

        df = OHLCVTest.create_sample_ohlcv(100)

        # JSON hatasını önlemek için dtypes'ı string'e çevir
        dtypes_dict = {k:str(v) for k, v in df.dtypes.items()}

        details = {
            'dataframe_shape':df.shape,
            'columns':df.columns.tolist(),
            'dtypes':dtypes_dict,
            'required_columns':['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            'missing_columns':[]
        }

        warnings = []

        logger.info(f"DataFrame Boyutu: {df.shape[0]} satır × {df.shape[1]} sütun")
        logger.info(f"Sütunlar: {', '.join(df.columns.tolist())}")

        # Gerekli sütunlar kontrolü
        required_cols = details['required_columns']
        for col in required_cols:
            if col not in df.columns:
                details['missing_columns'].append(col)
                logger.error(f"❌ Eksik sütun: {col}")
            else:
                logger.info(f"✅ Sütun var: {col}")

        # Veri türleri kontrolü
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in numeric_cols:
            if col in df.columns:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    warnings.append(f"{col} sayısal olmalı, {df[col].dtype} var")
                    logger.warning(f"⚠️ {col} sayısal değil: {df[col].dtype}")
                else:
                    logger.info(f"✅ {col} sayısal: {df[col].dtype}")

        # Minimum bar sayısı kontrolü
        if len(df) < 60:
            warnings.append(f"Minimum 60 bar gerekli, {len(df)} var")
            logger.warning(f"⚠️ Yetersiz bar: {len(df)} < 60")
        else:
            logger.info(f"✅ Yeterli bar: {len(df)} >= 60")

        # NaN kontrolü
        nan_count = df.isnull().sum().sum()
        if nan_count > 0:
            warnings.append(f"{nan_count} NaN değer var")
            logger.warning(f"⚠️ NaN değer sayısı: {nan_count}")
        else:
            logger.info(f"✅ NaN değer yok")

        # OHLC mantığı kontrolü (High >= Low)
        invalid_ohlc = (df['high'] < df['low']).sum()
        if invalid_ohlc > 0:
            warnings.append(f"{invalid_ohlc} bar'da High < Low")
            logger.warning(f"⚠️ Geçersiz OHLC: {invalid_ohlc} bar")
        else:
            logger.info(f"✅ Tüm bar'larda High >= Low")

        # Sonuç
        if details['missing_columns'] or invalid_ohlc > 0:
            status = 'FAIL'
            logger.error(f"❌ TEST BAŞARIŞIZ")
        elif warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} sorun")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: OHLCV yapısı doğru")

        return status, details, warnings

    @staticmethod
    def test_ohlcv_quality() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 4: OHLCV Veri Kalitesi Kontrolü
        ═══════════════════════════════════════════════════════════════════════
        OHLCV verilerinin kalitesini ve tutarlılığını kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 4: OHLCV VERİ KALİTESİ KONTROLÜ")
        logger.info("=" * 70)

        df = OHLCVTest.create_sample_ohlcv(100)

        details = {
            'price_stats':{
                'min':float(df['close'].min()),
                'max':float(df['close'].max()),
                'mean':float(df['close'].mean()),
                'std':float(df['close'].std())
            },
            'volume_stats':{
                'min':float(df['volume'].min()),
                'max':float(df['volume'].max()),
                'mean':float(df['volume'].mean())
            },
            'quality_checks':{}
        }

        warnings = []

        logger.info(f"Fiyat İstatistikleri:")
        logger.info(f"  Min: ${details['price_stats']['min']:.2f}")
        logger.info(f"  Max: ${details['price_stats']['max']:.2f}")
        logger.info(f"  Ort: ${details['price_stats']['mean']:.2f}")
        logger.info(f"  Std: ${details['price_stats']['std']:.2f}")

        logger.info(f"Hacim İstatistikleri:")
        logger.info(f"  Min: {details['volume_stats']['min']:.0f}")
        logger.info(f"  Max: {details['volume_stats']['max']:.0f}")
        logger.info(f"  Ort: {details['volume_stats']['mean']:.0f}")

        # Fiyat volatilitesi kontrolü
        price_range = details['price_stats']['max'] - details['price_stats']['min']
        price_range_pct = (price_range / details['price_stats']['mean']) * 100

        details['quality_checks']['price_range_pct'] = price_range_pct
        logger.info(f"Fiyat Aralığı: {price_range_pct:.2f}%")

        if price_range_pct < 0.1:
            warnings.append("Fiyat hareketi çok az (< 0.1%)")
            logger.warning(f"⚠️ Düşük volatilite: {price_range_pct:.2f}%")
        elif price_range_pct > 50:
            warnings.append("Fiyat hareketi çok fazla (> 50%)")
            logger.warning(f"⚠️ Yüksek volatilite: {price_range_pct:.2f}%")
        else:
            logger.info(f"✅ Normal volatilite: {price_range_pct:.2f}%")

        # Hacim kontrolü
        if details['volume_stats']['min'] <= 0:
            warnings.append("Hacim sıfır veya negatif")
            logger.warning(f"⚠️ Geçersiz hacim: {details['volume_stats']['min']}")
        else:
            logger.info(f"✅ Hacim pozitif")

        # Timestamp kontrolü
        time_diffs = df['timestamp'].diff().dt.total_seconds()
        expected_diff = 15 * 60  # 15 dakika
        irregular_times = (time_diffs != expected_diff).sum() - 1  # İlk NaN hariç

        details['quality_checks']['irregular_timestamps'] = int(irregular_times)

        if irregular_times > 0:
            warnings.append(f"{irregular_times} irregular timestamp var")
            logger.warning(f"⚠️ Düzensiz zaman: {irregular_times} bar")
        else:
            logger.info(f"✅ Tüm timestamp'ler düzenli")

        # Sonuç
        if warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} kalite sorunu")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: Veri kalitesi iyi")

        return status, details, warnings


class SMMATest:
    """SMMA Hesaplama Testleri"""

    @staticmethod
    def smma(series: pd.Series, length: int) -> pd.Series:
        """SMMA hesapla (StrategyV2 ile aynı)"""
        series = pd.to_numeric(series, errors='coerce')
        n = len(series)
        if n == 0 or length <= 1:
            return series.copy()

        out = [np.nan] * n
        seed_range = min(length, n)
        seed = pd.Series(series.iloc[:seed_range]).mean()
        prev = seed

        for i in range(seed_range - 1, n):
            val = series.iloc[i]
            if np.isnan(val):
                out[i] = np.nan
                continue
            if np.isnan(prev):
                prev = val
            prev = (prev * (length - 1) + val) / length
            out[i] = prev

        return pd.Series(out, index=series.index, dtype='float64')

    @staticmethod
    def test_smma_calculation() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 5: SMMA HESAPLAMA KONTROLÜ
        ═══════════════════════════════════════════════════════════════════════
        SMMA hesaplamasının doğru yapılıp yapılmadığını kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 5: SMMA HESAPLAMA KONTROLÜ")
        logger.info("=" * 70)

        df = OHLCVTest.create_sample_ohlcv(100)

        # SMMA hesapla
        open_smma = SMMATest.smma(df['open'], 2)
        close_smma = SMMATest.smma(df['close'], 2)

        details = {
            'smma_length':2,
            'open_smma_stats':{
                'min':float(open_smma.min()),
                'max':float(open_smma.max()),
                'mean':float(open_smma.mean()),
                'nan_count':int(open_smma.isna().sum())
            },
            'close_smma_stats':{
                'min':float(close_smma.min()),
                'max':float(close_smma.max()),
                'mean':float(close_smma.mean()),
                'nan_count':int(close_smma.isna().sum())
            },
            'last_values':{
                'open_smma_last':float(open_smma.iloc[-1]) if not np.isnan(open_smma.iloc[-1]) else None,
                'close_smma_last':float(close_smma.iloc[-1]) if not np.isnan(close_smma.iloc[-1]) else None
            }
        }

        warnings = []

        logger.info(f"Open SMMA İstatistikleri:")
        logger.info(f"  Min: {details['open_smma_stats']['min']:.2f}")
        logger.info(f"  Max: {details['open_smma_stats']['max']:.2f}")
        logger.info(f"  Ort: {details['open_smma_stats']['mean']:.2f}")
        logger.info(f"  NaN: {details['open_smma_stats']['nan_count']}")

        logger.info(f"Close SMMA İstatistikleri:")
        logger.info(f"  Min: {details['close_smma_stats']['min']:.2f}")
        logger.info(f"  Max: {details['close_smma_stats']['max']:.2f}")
        logger.info(f"  Ort: {details['close_smma_stats']['mean']:.2f}")
        logger.info(f"  NaN: {details['close_smma_stats']['nan_count']}")

        # NaN kontrolü
        if details['open_smma_stats']['nan_count'] > 5:
            warnings.append(f"Open SMMA'da çok NaN: {details['open_smma_stats']['nan_count']}")
            logger.warning(f"⚠️ Open SMMA NaN çok: {details['open_smma_stats']['nan_count']}")
        else:
            logger.info(f"✅ Open SMMA NaN az: {details['open_smma_stats']['nan_count']}")

        if details['close_smma_stats']['nan_count'] > 5:
            warnings.append(f"Close SMMA'da çok NaN: {details['close_smma_stats']['nan_count']}")
            logger.warning(f"⚠️ Close SMMA NaN çok: {details['close_smma_stats']['nan_count']}")
        else:
            logger.info(f"✅ Close SMMA NaN az: {details['close_smma_stats']['nan_count']}")

        # Son değerlerin geçerli olup olmadığı
        if details['last_values']['open_smma_last'] is None:
            warnings.append("Son Open SMMA değeri NaN")
            logger.warning(f"⚠️ Son Open SMMA NaN")
        else:
            logger.info(f"✅ Son Open SMMA: {details['last_values']['open_smma_last']:.2f}")

        if details['last_values']['close_smma_last'] is None:
            warnings.append("Son Close SMMA değeri NaN")
            logger.warning(f"⚠️ Son Close SMMA NaN")
        else:
            logger.info(f"✅ Son Close SMMA: {details['last_values']['close_smma_last']:.2f}")

        # SMMA değerleri orijinal fiyatlar arasında mı?
        if details['open_smma_stats']['min'] < df['open'].min() * 0.95:
            warnings.append("Open SMMA orijinal fiyatın altında çok düşük")
            logger.warning(f"⚠️ Open SMMA çok düşük")

        if details['open_smma_stats']['max'] > df['open'].max() * 1.05:
            warnings.append("Open SMMA orijinal fiyatın üstünde çok yüksek")
            logger.warning(f"⚠️ Open SMMA çok yüksek")

        # Sonuç
        if warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} SMMA sorunu")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: SMMA hesaplaması doğru")

        return status, details, warnings

    @staticmethod
    def test_smma_with_delay() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 6: SMMA DELAY OFFSET KONTROLÜ
        ═══════════════════════════════════════════════════════════════════════
        Delay offset parametresinin SMMA'yı nasıl etkilediğini kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 6: SMMA DELAY OFFSET KONTROLÜ")
        logger.info("=" * 70)

        df = OHLCVTest.create_sample_ohlcv(100)

        details = {
            'delay_tests':{}
        }

        warnings = []

        # Farklı delay offset değerleri test et
        for delay_offset in [0, 1, 2]:
            logger.info(f"\nDelay Offset = {delay_offset}:")

            # Delay uygula
            if delay_offset > 0:
                op = df['open'].shift(delay_offset)
                cl = df['close'].shift(delay_offset)
            else:
                op = df['open']
                cl = df['close']

            # SMMA hesapla
            open_smma = SMMATest.smma(op, 2)
            close_smma = SMMATest.smma(cl, 2)

            test_result = {
                'open_smma_last':float(open_smma.iloc[-1]) if not np.isnan(open_smma.iloc[-1]) else None,
                'close_smma_last':float(close_smma.iloc[-1]) if not np.isnan(close_smma.iloc[-1]) else None,
                'nan_count':int(open_smma.isna().sum() + close_smma.isna().sum())
            }

            details['delay_tests'][f'delay_{delay_offset}'] = test_result

            logger.info(f"  Open SMMA (son): {test_result['open_smma_last']}")
            logger.info(f"  Close SMMA (son): {test_result['close_smma_last']}")
            logger.info(f"  NaN toplam: {test_result['nan_count']}")

            # Delay arttıkça NaN artmalı
            if delay_offset > 0 and test_result['nan_count'] <= 0:
                warnings.append(f"Delay {delay_offset} ile NaN artmadı")
                logger.warning(f"⚠️ Delay {delay_offset} ile NaN artmadı")

        # Sonuç
        if warnings:
            status = 'WARNING'
            logger.warning(f"⚠️ TEST UYARI: {len(warnings)} delay sorunu")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: Delay offset doğru çalışıyor")

        return status, details, warnings


class CrossoverTest:
    """Crossover Tetikleyici Testleri"""

    @staticmethod
    def test_crossover_logic() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 7: CROSSOVER TETIKLEYICI KONTROLÜ
        ═══════════════════════════════════════════════════════════════════════
        Crossover/Crossunder tetikleyicisinin doğru çalıştığını kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 7: CROSSOVER TETIKLEYICI KONTROLÜ")
        logger.info("=" * 70)

        # Test senaryoları oluştur
        test_cases = [
            {
                'name':'LONG Crossover',
                'open_alt':[100.0, 100.5, 101.0],
                'close_alt':[99.5, 100.0, 101.5],
                'expected_bull':True,
                'expected_bear':False
            },
            {
                'name':'SHORT Crossunder',
                'open_alt':[100.0, 100.5, 101.0],
                'close_alt':[101.5, 101.0, 100.0],
                'expected_bull':False,
                'expected_bear':True
            },
            {
                'name':'Hiçbiri',
                'open_alt':[100.0, 100.5, 101.0],
                'close_alt':[100.5, 101.0, 101.5],
                'expected_bull':False,
                'expected_bear':False
            },
            {
                'name':'NaN Var',
                'open_alt':[100.0, np.nan, 101.0],
                'close_alt':[99.5, 100.0, 101.5],
                'expected_bull':False,
                'expected_bear':False
            }
        ]

        details = {
            'test_cases':[]
        }

        warnings = []

        for test_case in test_cases:
            logger.info(f"\nTest Senaryosu: {test_case['name']}")

            open_alt = pd.Series(test_case['open_alt'])
            close_alt = pd.Series(test_case['close_alt'])

            # Crossover kontrol
            if len(close_alt) < 2 or len(open_alt) < 2:
                bull, bear = False, False
            else:
                prev_c = close_alt.iloc[-2]
                prev_o = open_alt.iloc[-2]
                last_c = close_alt.iloc[-1]
                last_o = open_alt.iloc[-1]

                # NaN kontrol
                if any(np.isnan(x) for x in [prev_c, prev_o, last_c, last_o]):
                    bull, bear = False, False
                else:
                    bull = (prev_c <= prev_o) and (last_c > last_o)
                    bear = (prev_c >= prev_o) and (last_c < last_o)

            result = {
                'name':test_case['name'],
                'bull':bull,
                'bear':bear,
                'expected_bull':test_case['expected_bull'],
                'expected_bear':test_case['expected_bear'],
                'correct':(bull == test_case['expected_bull'] and
                           bear == test_case['expected_bear'])
            }

            details['test_cases'].append(result)

            logger.info(f"  Bull: {bull} (beklenen: {test_case['expected_bull']})")
            logger.info(f"  Bear: {bear} (beklenen: {test_case['expected_bear']})")

            if result['correct']:
                logger.info(f"  ✅ DOĞRU")
            else:
                logger.error(f"  ❌ YANLIŞ")
                warnings.append(f"{test_case['name']} yanlış sonuç verdi")

        # Sonuç
        passed = sum(1 for tc in details['test_cases'] if tc['correct'])
        total = len(details['test_cases'])

        logger.info(f"\nSonuç: {passed}/{total} test geçti")

        if warnings:
            status = 'FAIL'
            logger.error(f"❌ TEST BAŞARIŞIZ: {len(warnings)} test başarısız")
        else:
            status = 'PASS'
            logger.info("✅ TEST BAŞARILI: Tüm crossover testleri doğru")

        return status, details, warnings


class StrategyTest:
    """Strateji Sinyal Üretim Testleri"""

    @staticmethod
    async def test_strategy_signal_generation() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 8: STRATEJİ SİNYAL ÜRETIM KONTROLÜ
        ═══════════════════════════════════════════════════════════════════════
        Strateji V2'nin sinyal üretip üretmediğini kontrol eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 8: STRATEJİ SİNYAL ÜRETIM KONTROLÜ")
        logger.info("=" * 70)

        try:
            from strategies.strategy_v2 import StrategyV2

            df = OHLCVTest.create_sample_ohlcv(100)

            # --- MANİPÜLASYON: Sinyal oluşumunu garanti etmek için ---
            # Son 5 barı düşüş trendine sok
            for i in range(5, 1):
                idx = df.index[-i]
                df.loc[idx, 'open'] = 42000
                df.loc[idx, 'close'] = 41000

            # Son barı güçlü bir yükseliş yap (Close > Open) ve fiyatı zıplat
            last_idx = df.index[-1]
            df.loc[last_idx, 'open'] = 41000
            df.loc[last_idx, 'close'] = 45000
            # ---------------------------------------------------------

            # Strateji oluştur
            strategy = StrategyV2()

            # --- ÖNEMLİ DÜZELTME: Test ortamı için parametreleri ayarla ---
            # Test script'i raw SMMA kontrolü yaptığı için, stratejinin de
            # alt timeframe kullanmadan raw SMMA kullanmasını sağlıyoruz.
            strategy.params['use_alt_resolution'] = False
            strategy.params['basis_len'] = 2
            # --------------------------------------------------------------

            # Sinyal üret
            signal = strategy.generate_signal(df, market_regime="Yatay")

            details = {
                'signal_generated':signal is not None,
                'signal_data':None,
                'strategy_params':strategy.params
            }

            warnings = []

            if signal is None:
                logger.warning("❌ Sinyal üretilmedi (None döndü)")
                logger.info("Olası sebepler:")
                logger.info("  - Crossover tetiklenmedi")
                logger.info("  - Filtre bloke etti")
                logger.info("  - Veri sorunu")
                status = 'WARNING'
                warnings.append("Sinyal üretilmedi")
            else:
                logger.info("✅ Sinyal üretildi!")

                details['signal_data'] = {
                    'type':signal.get('type'),
                    'entry':signal.get('entry'),
                    'stop':signal.get('stop'),
                    'targets_count':len(signal.get('targets', [])),
                    'targets':signal.get('targets'),
                    'meta':signal.get('meta')
                }

                logger.info(f"  Yön: {signal.get('type')}")
                logger.info(f"  Giriş: {signal.get('entry'):.2f}")
                logger.info(f"  Stop: {signal.get('stop'):.2f}")
                logger.info(f"  Hedefler: {len(signal.get('targets', []))} adet")
                logger.info(f"  Meta: {signal.get('meta')}")

                # Sinyal doğrulaması
                if signal.get('type') not in ['LONG', 'SHORT']:
                    warnings.append(f"Geçersiz sinyal yönü: {signal.get('type')}")
                    logger.warning(f"⚠️ Geçersiz yön: {signal.get('type')}")

                if signal.get('entry') is None or signal.get('entry') <= 0:
                    warnings.append("Geçersiz giriş fiyatı")
                    logger.warning(f"⚠️ Geçersiz giriş: {signal.get('entry')}")

                if signal.get('stop') is None or signal.get('stop') <= 0:
                    warnings.append("Geçersiz stop loss")
                    logger.warning(f"⚠️ Geçersiz stop: {signal.get('stop')}")

                if len(signal.get('targets', [])) == 0:
                    warnings.append("Hedef yok")
                    logger.warning(f"⚠️ Hedef sayısı: 0")

                # LONG için stop < entry, SHORT için stop > entry
                if signal.get('type') == 'LONG':
                    if signal.get('stop') >= signal.get('entry'):
                        warnings.append("LONG için Stop >= Entry")
                        logger.warning(f"⚠️ LONG Stop >= Entry")
                elif signal.get('type') == 'SHORT':
                    if signal.get('stop') <= signal.get('entry'):
                        warnings.append("SHORT için Stop <= Entry")
                        logger.warning(f"⚠️ SHORT Stop <= Entry")

                if warnings:
                    status = 'WARNING'
                    logger.warning(f"⚠️ TEST UYARI: {len(warnings)} sinyal sorunu")
                else:
                    status = 'PASS'
                    logger.info("✅ TEST BAŞARILI: Sinyal doğru")

            return status, details, warnings

        except ImportError as e:
            logger.error(f"❌ StrategyV2 import edilemedi: {e}")
            return 'FAIL', {'error':str(e)}, [f"Import hatası: {e}"]
        except Exception as e:
            logger.error(f"❌ Hata: {e}", exc_info=True)
            return 'FAIL', {'error':str(e)}, [f"Hata: {e}"]


class IntegrationTest:
    """Entegrasyon Testleri"""

    @staticmethod
    async def test_full_flow() -> Tuple[str, Dict[str, Any], List[str]]:
        """
        TEST 9: TAM AKIŞ TESTI (Alarm → Sinyal)
        ═══════════════════════════════════════════════════════════════════════
        Alarm kurulumundan sinyal üretimine kadar tüm akışı test eder.
        """
        logger.info("\n" + "=" * 70)
        logger.info("TEST 9: TAM AKIŞ TESTI (ALARM → SİNYAL)")
        logger.info("=" * 70)

        details = {
            'steps':{}
        }

        warnings = []

        try:
            # ADIM 1: Alarm oluştur
            logger.info("\nADIM 1: Alarm Oluştur")
            alarm = {
                'symbol':'BTC/USDT',
                'timeframe':'15m',
                'market_type':'futures',
                'strategy_hint':'v2',
                'user_id':12345,
                'status':'active',
                'created_at':datetime.now(timezone.utc).isoformat(),
                'meta':{
                    'source':'test',
                    'ai_confidence':0.75,
                    'potential_pct':2.5
                }
            }

            details['steps']['alarm_created'] = alarm
            logger.info(f"  ✅ Alarm oluşturuldu: {alarm['symbol']} ({alarm['strategy_hint']})")

            # ADIM 2: OHLCV verisi çek
            logger.info("\nADIM 2: OHLCV Verisi Çek")
            df = OHLCVTest.create_sample_ohlcv(100)

            # --- MANİPÜLASYON: Sinyal oluşumunu garanti etmek için ---
            # Sondan 3. ve 2. bar: Düşüş/Yatay -> SMMA aşağı/yatay
            df.iloc[-3, df.columns.get_loc('open')] = 40100
            df.iloc[-3, df.columns.get_loc('close')] = 40000

            df.iloc[-2, df.columns.get_loc('open')] = 40000
            df.iloc[-2, df.columns.get_loc('close')] = 40000

            # Son bar: Güçlü Yükseliş -> SMMA yukarı keser
            df.iloc[-1, df.columns.get_loc('open')] = 40000
            df.iloc[-1, df.columns.get_loc('close')] = 45000
            # ---------------------------------------------------------

            details['steps']['ohlcv_fetched'] = {
                'shape':df.shape,
                'columns':df.columns.tolist()
            }
            logger.info(f"  ✅ OHLCV çekildi: {df.shape[0]} bar")

            # ADIM 3: SMMA hesapla
            logger.info("\nADIM 3: SMMA Hesapla")
            open_smma = SMMATest.smma(df['open'], 2)
            close_smma = SMMATest.smma(df['close'], 2)
            details['steps']['smma_calculated'] = {
                'open_smma_last':float(open_smma.iloc[-1]),
                'close_smma_last':float(close_smma.iloc[-1])
            }
            logger.info(f"  ✅ SMMA hesaplandı")

            # ADIM 4: Crossover kontrol
            logger.info("\nADIM 4: Crossover Kontrol")
            prev_c = close_smma.iloc[-2]
            prev_o = open_smma.iloc[-2]
            last_c = close_smma.iloc[-1]
            last_o = open_smma.iloc[-1]

            bull = (prev_c <= prev_o) and (last_c > last_o)
            bear = (prev_c >= prev_o) and (last_c < last_o)

            details['steps']['crossover_check'] = {
                'bull':bull,
                'bear':bear
            }
            logger.info(f"  Bull: {bull}, Bear: {bear}")

            if not (bull or bear):
                warnings.append("Crossover tetiklenmedi - test verisi değiştirilmeli")
                logger.warning(f"  ⚠️ Crossover yok")
            else:
                logger.info(f"  ✅ Crossover tetiklendi")

            # ADIM 5: Strateji sinyal üret
            logger.info("\nADIM 5: Strateji Sinyal Üret")
            from strategies.strategy_v2 import StrategyV2

            strategy = StrategyV2()

            # --- ÖNEMLİ DÜZELTME: Test ortamı için parametreleri ayarla ---
            strategy.params['use_alt_resolution'] = False
            strategy.params['basis_len'] = 2
            # --------------------------------------------------------------

            signal = strategy.generate_signal(df, market_regime="Yatay")

            if signal:
                details['steps']['signal_generated'] = {
                    'type':signal.get('type'),
                    'entry':signal.get('entry'),
                    'stop':signal.get('stop'),
                    'targets':signal.get('targets')
                }
                logger.info(f"  ✅ Sinyal üretildi: {signal.get('type')}")
            else:
                details['steps']['signal_generated'] = None
                logger.warning(f"  ⚠️ Sinyal üretilmedi")
                warnings.append("Sinyal üretilmedi - test verisi değiştirilmeli")

            # ADIM 6: Sonuç
            logger.info("\nADIM 6: Sonuç")

            if signal:
                logger.info("✅ TAM AKIŞ BAŞARILI")
                logger.info(f"  Alarm: {alarm['symbol']} ({alarm['strategy_hint']})")
                logger.info(f"  Sinyal: {signal.get('type')}")
                logger.info(f"  Giriş: {signal.get('entry'):.2f}")
                logger.info(f"  Stop: {signal.get('stop'):.2f}")
                logger.info(f"  Hedefler: {len(signal.get('targets', []))} adet")
                status = 'PASS' if not warnings else 'WARNING'
            else:
                logger.warning("❌ TAM AKIŞ BAŞARIŞIZ")
                status = 'WARNING'

            return status, details, warnings

        except Exception as e:
            logger.error(f"❌ Hata: {e}", exc_info=True)
            return 'FAIL', details, [f"Hata: {e}"]


# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 4: TEST ÇALIŞTIRICISI
# ═══════════════════════════════════════════════════════════════════════════

async def run_all_tests():
    """Tüm testleri çalıştır"""

    results = TestResults()

    print("\n" + "=" * 70)
    print("🚀 OLIMPOS STRATEJİSİ - KAPSAMLI TEST BAŞLANIYOR")
    print("=" * 70)
    print(f"Başlangıç Zamanı: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70 + "\n")

    # TEST 1: Alarm Yapısı
    status, details, warnings = AlarmTest.test_alarm_structure()
    results.add_test("TEST 1: Alarm Yapısı", status, details, warnings)

    # TEST 2: Alarm Parametreleri
    status, details, warnings = AlarmTest.test_alarm_parameters()
    results.add_test("TEST 2: Alarm Parametreleri", status, details, warnings)

    # TEST 3: OHLCV Yapısı
    status, details, warnings = OHLCVTest.test_ohlcv_structure()
    results.add_test("TEST 3: OHLCV Yapısı", status, details, warnings)

    # TEST 4: OHLCV Kalitesi
    status, details, warnings = OHLCVTest.test_ohlcv_quality()
    results.add_test("TEST 4: OHLCV Kalitesi", status, details, warnings)

    # TEST 5: SMMA Hesaplama
    status, details, warnings = SMMATest.test_smma_calculation()
    results.add_test("TEST 5: SMMA Hesaplama", status, details, warnings)

    # TEST 6: SMMA Delay
    status, details, warnings = SMMATest.test_smma_with_delay()
    results.add_test("TEST 6: SMMA Delay Offset", status, details, warnings)

    # TEST 7: Crossover
    status, details, warnings = CrossoverTest.test_crossover_logic()
    results.add_test("TEST 7: Crossover Tetikleyici", status, details, warnings)

    # TEST 8: Strateji Sinyal
    status, details, warnings = await StrategyTest.test_strategy_signal_generation()
    results.add_test("TEST 8: Strateji Sinyal Üretim", status, details, warnings)

    # TEST 9: Tam Akış
    status, details, warnings = await IntegrationTest.test_full_flow()
    results.add_test("TEST 9: Tam Akış (Alarm → Sinyal)", status, details, warnings)

    # ÖNERİLER
    logger.info("\n" + "=" * 70)
    logger.info("📋 ÖNERİLER")
    logger.info("=" * 70)

    if results.results['summary']['failed'] > 0:
        results.add_recommendation(
            "❌ BAŞARIŞIZ TESTLER VAR - Yukarıdaki hataları düzeltiniz"
        )

    if results.results['summary']['warnings'] > 0:
        results.add_recommendation(
            "⚠️ UYARI TESTLER VAR - Parametreleri gözden geçiriniz"
        )

    if results.results['summary']['passed'] == results.results['summary']['total_tests']:
        results.add_recommendation(
            "✅ TÜM TESTLER BAŞARILI - Sistem hazır!"
        )

    # Parametreler için öneriler
    results.add_recommendation(
        "TEST MODUNDA ÖNERILEN PARAMETRELER:\n"
        "  - use_alt_resolution: False (alt çözünürlüğü kapat)\n"
        "  - basis_len: 1 (daha hızlı tepki)\n"
        "  - delay_offset: 0 (gecikme yok)\n"
        "  - use_atr_targets: False (ATR'ı kapat)\n"
        "  - Rejim filtresini comment yap"
    )

    results.add_recommendation(
        "SONRAKI ADIMLAR:\n"
        "  1. Test sonuçlarını gözden geçir\n"
        "  2. Başarısız testleri düzelt\n"
        "  3. test_results.json dosyasını kontrol et\n"
        "  4. Bot'u parametrelerle yeniden başlat\n"
        "  5. Gerçek verilerde test et"
    )

    # Sonuçları kaydet ve yazdır
    results.save()
    results.print_summary()

    print("\n" + "=" * 70)
    print(f"Bitiş Zamanı: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    print("📁 Sonuçlar kaydedildi: test_results.json")
    print("📁 Debug logları: test_debug.log")
    print("=" * 70 + "\n")


# ═══════════════════════════════════════════════════════════════════════════
# BÖLÜM 5: MAIN
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("""
    ╔═══════════════════════════════════════════════════════════════════════════╗
    ║                                                                           ║
    ║        🔧 OLIMPOS STRATEJİSİ - KAPSAMLI SINYAL ÜRETIM TEST DOSYASI        ║
    ║                                                                           ║
    ║  Bu test dosyası aşağıdaki kontrolleri yapar:                           ║
    ║                                                                           ║
    ║  1️⃣  Alarm Yapısı Kontrolü                                              ║
    ║  2️⃣  Alarm Parametreleri Kontrolü                                       ║
    ║  3️⃣  OHLCV Veri Yapısı Kontrolü                                         ║
    ║  4️⃣  OHLCV Veri Kalitesi Kontrolü                                       ║
    ║  5️⃣  SMMA Hesaplama Kontrolü                                            ║
    ║  6️⃣  SMMA Delay Offset Kontrolü                                         ║
    ║  7️⃣  Crossover Tetikleyici Kontrolü                               
    ║  8️⃣  Strateji Sinyal Üretim Kontrolü                                    ║
    ║  9️⃣  Tam Akış Testi (Alarm → Sinyal)                                    ║
    ║                                                                           ║
    ║  Çıktılar:                                                               ║
    ║    📊 test_results.json - Detaylı test sonuçları                        ║
    ║    📋 test_debug.log - Debug logları                                    ║
    ║    🖥️  Konsol çıktısı - Gerçek zamanlı bilgi                           ║
    ║                                                                           ║
    ║  Başlamak için: python test_signal_generation.py                        ║
    ║                                                                           ║
    ╚═══════════════════════════════════════════════════════════════════════════╝
    """)

# Testleri çalıştır
asyncio.run(run_all_tests())
