# strategies/trend_analyzer.py
"""
✅ Global & Yerel Trend Karşılaştırması ve Özet Analiz
"""
import logging
from typing import Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class TrendType(Enum):
    """Trend Türleri"""
    UPTREND = "📈 Yükseliş"
    DOWNTREND = "📉 Düşüş"
    NEUTRAL = "⚪️ Nötr"
    UNKNOWN = "❓ Bilinmiyor"


class TrendAnalyzer:
    """Global ve Yerel Trend Analizi"""

    @staticmethod
    def parse_trend(trend_str: str) -> TrendType:
        """Trend string'ini enum'a dönüştür"""
        trend_str_lower = trend_str.lower()
        if "yükseliş" in trend_str_lower or "📈" in trend_str:
            return TrendType.UPTREND
        elif "düşüş" in trend_str_lower or "📉" in trend_str:
            return TrendType.DOWNTREND
        elif "nötr" in trend_str_lower or "⚪️" in trend_str:
            return TrendType.NEUTRAL
        else:
            return TrendType.UNKNOWN

    @staticmethod
    def compare_trends(global_trend: str, local_trend: str) -> Dict[str, Any]:
        """
        Global ve Yerel Trendi Karşılaştır

        Returns:
            {
                'scenario': str,
                'recommendation': str,
                'risk_level': str,
                'action': str,
                'confidence': float,
                'description': str
            }
        """
        g_trend = TrendAnalyzer.parse_trend(global_trend)
        l_trend = TrendAnalyzer.parse_trend(local_trend)

        # Senaryo 1: Global Yükseliş + Yerel Yükseliş
        if g_trend == TrendType.UPTREND and l_trend == TrendType.UPTREND:
            return {
                'scenario': '🚀 GÜÇLÜ YÜKSELIŞ',
                'recommendation': 'LONG İŞLEM GİRİŞİ',
                'risk_level': '🟢 Düşük',
                'action': 'LONG - Agresif Al',
                'confidence': 0.95,
                'description': 'Global ve yerel trend uyumlu, güçlü yükseliş sinyali'
            }

        # Senaryo 2: Global Yükseliş + Yerel Düşüş
        elif g_trend == TrendType.UPTREND and l_trend == TrendType.DOWNTREND:
            return {
                'scenario': '⚡️ DÜŞÜŞ İÇİNDE YÜKSELIŞ',
                'recommendation': 'LONG İŞLEM (Dikkatli)',
                'risk_level': '🟡 Orta',
                'action': 'LONG - Destek Seviyesinde Al',
                'confidence': 0.65,
                'description': 'Global yükseliş içinde yerel düşüş - Geri çekilme fırsatı'
            }

        # Senaryo 3: Global Yükseliş + Yerel Nötr
        elif g_trend == TrendType.UPTREND and l_trend == TrendType.NEUTRAL:
            return {
                'scenario': '📊 NÖTR BÖLGEDE YÜKSELIŞ',
                'recommendation': 'LONG İŞLEM (Bekleme)',
                'risk_level': '🟡 Orta',
                'action': 'LONG - Sinyal Bekle',
                'confidence': 0.60,
                'description': 'Global yükseliş devam ediyor, yerel trend belirlenmemiş'
            }

        # Senaryo 4: Global Düşüş + Yerel Düşüş
        elif g_trend == TrendType.DOWNTREND and l_trend == TrendType.DOWNTREND:
            return {
                'scenario': '📉 GÜÇLÜ DÜŞÜŞ',
                'recommendation': 'SHORT İŞLEM GİRİŞİ',
                'risk_level': '🟢 Düşük',
                'action': 'SHORT - Agresif Sat',
                'confidence': 0.95,
                'description': 'Global ve yerel trend uyumlu, güçlü düşüş sinyali'
            }

        # Senaryo 5: Global Düşüş + Yerel Yükseliş
        elif g_trend == TrendType.DOWNTREND and l_trend == TrendType.UPTREND:
            return {
                'scenario': '⚡️ YÜKSELIŞ İÇİNDE DÜŞÜŞ',
                'recommendation': 'SHORT İŞLEM (Dikkatli)',
                'risk_level': '🔴 Yüksek',
                'action': 'SHORT - Direnç Seviyesinde Sat',
                'confidence': 0.55,
                'description': 'Global düşüş içinde yerel yükseliş - Riskli SHORT'
            }

        # Senaryo 6: Global Düşüş + Yerel Nötr
        elif g_trend == TrendType.DOWNTREND and l_trend == TrendType.NEUTRAL:
            return {
                'scenario': '📊 NÖTR BÖLGEDE DÜŞÜŞ',
                'recommendation': 'SHORT İŞLEM (Bekleme)',
                'risk_level': '🟡 Orta',
                'action': 'SHORT - Sinyal Bekle',
                'confidence': 0.60,
                'description': 'Global düşüş devam ediyor, yerel trend belirlenmemiş'
            }

        # Senaryo 7: Global Nötr
        elif g_trend == TrendType.NEUTRAL:
            return {
                'scenario': '⚪️ NÖTR PİYASA',
                'recommendation': 'BEKLEME',
                'risk_level': '🟡 Orta',
                'action': 'WAIT - Sinyal Bekle',
                'confidence': 0.40,
                'description': 'Global trend nötr, net sinyal yok'
            }

        # Senaryo 8: Bilinmiyor
        else:
            return {
                'scenario': '❓ BİLİNMEYEN DURUM',
                'recommendation': 'BEKLEME',
                'risk_level': '🔴 Yüksek',
                'action': 'WAIT - Veri Bekle',
                'confidence': 0.20,
                'description': 'Trend verileri yetersiz, işlem yapılmamalı'
            }

    @staticmethod
    def generate_analysis_summary(
            symbol: str,
            global_trend: str,
            indicators: Dict[str, Any],
            fear_greed: int,
            strategy: str
    ) -> str:
        """
        Detaylı Analiz Özeti Oluştur
        """
        trend_analysis = TrendAnalyzer.compare_trends(
            global_trend,
            indicators.get('local_trend', '⚪️ Bilinmiyor')
        )

        adx = indicators.get('adx', 0.0)
        rsi = indicators.get('rsi', 50.0)
        momentum = indicators.get('momentum_tension', 0.0)
        vol_ratio = indicators.get('volume_ratio', 1.0)
        bb_width = indicators.get('bb_width', 0.0)

        summary = f"""
╔════════════════════════════════════════════╗
║     📊 DETAYLI ANALİZ ÖZETI - {symbol}     ║
╚════════════════════════════════════════════╝

🎯 SENARYO: {trend_analysis['scenario']}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🌍 TREND KARŞILAŞTIRMASI:
  • Global Trend    : {global_trend}
  • Yerel Trend     : {indicators.get('local_trend', '⚪️ Bilinmiyor')}
  • Uyum Durumu     : {trend_analysis['scenario']}

📈 TEKNİK GÖSTERGELER:
  • ADX (Trend Gücü): {adx:.1f} {indicators.get('adx_status', '💤 Zayıf')}
  • RSI             : {rsi:.1f} {indicators.get('rsi_status', '⚪️ Nötr')}
  • Momentum        : {momentum:.2f}% {indicators.get('momentum_status', '💤 Düşük')}
  • Hacim Oranı     : {vol_ratio:.2f}x {indicators.get('volume_status', '⚪️ Normal')}
  • BB Genişliği    : {bb_width:.4f} {indicators.get('bb_status', '🔐 Sıkışma')}
  • StochRSI K      : {indicators.get('stoch_k', 50.0):.1f} {indicators.get('stoch_status', '✅ Normal')}
  • OBV             : {indicators.get('obv_status', '⚪️ Nötr')}

😨 PİYASA DUYGUSALLIĞI:
  • Fear/Greed Index: {fear_greed} {'😨 Korku' if fear_greed < 50 else '😊 Açgözlülük'}

💡 KARAR VE ÖNERİ:
  • Senaryo         : {trend_analysis['scenario']}
  • Tavsiye         : {trend_analysis['recommendation']}
  • Risk Seviyesi   : {trend_analysis['risk_level']}
  • İşlem Türü      : {trend_analysis['action']}
  • Güven Oranı     : {trend_analysis['confidence'] * 100:.0f}%

📝 AÇIKLAMA:
  {trend_analysis['description']}

🔍 DETAYLI MANTIK:
"""
        # Detaylı mantık açıklaması
        if adx < 20:
            summary += f"  ⚠️ ADX {adx:.1f} - Trend gücü zayıf, volatilite düşük\n"
        elif adx > 30:
            summary += f"  ✅ ADX {adx:.1f} - Trend gücü güçlü, net yön var\n"

        if rsi > 70:
            summary += f"  🔴 RSI {rsi:.1f} - Aşırı alım, düşüş riski\n"
        elif rsi < 30:
            summary += f"  🟢 RSI {rsi:.1f} - Aşırı satım, yükseliş fırsatı\n"

        if momentum > 2.0:
            summary += f"  🚀 Momentum {momentum:.2f}% - Güçlü fiyat hareketi\n"
        elif momentum < 0.5:
            summary += f"  💤 Momentum {momentum:.2f}% - Zayıf hareket\n"

        if vol_ratio > 1.5:
            summary += f"  📊 Hacim {vol_ratio:.2f}x - Yüksek hacim, ilgi var\n"
        elif vol_ratio < 0.8:
            summary += f"  ⚪️ Hacim {vol_ratio:.2f}x - Düşük hacim, ilgi az\n"

        if bb_width > 0.05:
            summary += f"  📈 BB {bb_width:.4f} - Genişleme, volatilite yüksek\n"
        elif bb_width < 0.01:
            summary += f"  🔐 BB {bb_width:.4f} - Sıkışma, kırılma bekleniyor\n"

        summary += f"""
⚡ SONUÇ:
  {trend_analysis['action']}
  Risk Seviyesi: {trend_analysis['risk_level']}
  Güven: {trend_analysis['confidence'] * 100:.0f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        return summary
