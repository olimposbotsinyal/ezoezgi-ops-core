# config_service.py
import os
import logging
import json
import copy
from typing import Any, Optional

# === PATCH A.1: ConfigService ==================================================
class ConfigService:
    """
    Uygulama genelindeki konfigürasyonları yönetir.
    - Manuel ayarlar (kullanıcı tarafından değiştirilen): olimpos_tarama_ayarlari.json
    - Otomatik ayarlar (tuner tarafından yönetilen): StrategyAdaptiveTuner_ayarlari.json
    """
    MANUAL_CONFIG_PATH = os.path.join("config", "olimpos_tarama_ayarlari.json")
    AUTO_CONFIG_PATH = os.path.join("config", "StrategyAdaptiveTuner_ayarlari.json")
    _last_mtime = None
    _manual_data = {}
    _auto_data = {}
    _initialized = False
    @classmethod
    def init(cls, path=None):
        """Sınıfı başlatır. Opsiyonel olarak manuel konfigürasyon yolunu ayarlar."""
        if path:
            # DÜZELTME: Kullanılmayan _manual_path yerine doğrudan sınıf değişkenini güncelle.
            cls.MANUAL_CONFIG_PATH = path
            logging.info(f"[CONFIG] Manuel ayar yolu değiştirildi: {path}")
        cls.load(force=True)
        cls._initialized = True

    @classmethod
    def load(cls, force=False):
        """Hem manuel hem de otomatik ayar dosyalarını yükler."""
        try:
            # Manuel ayarları yükle
            if os.path.exists(cls.MANUAL_CONFIG_PATH):
                st = os.stat(cls.MANUAL_CONFIG_PATH)
                if force or cls._last_mtime is None or st.st_mtime != cls._last_mtime:
                    with open(cls.MANUAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                        cls._manual_data = json.load(f)
                    cls._last_mtime = st.st_mtime
                    logging.info(f"[CONFIG] Manuel ayarlar yüklendi: {cls.MANUAL_CONFIG_PATH}")
            else:
                cls._manual_data = {"control_mode": {"active": "manual"}} # Varsayılan
                cls.save_manual_config()

            # Otomatik ayarları yükle
            if os.path.exists(cls.AUTO_CONFIG_PATH):
                with open(cls.AUTO_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cls._auto_data = json.load(f)
                logging.info(f"[CONFIG] Otomatik ayarlar yüklendi: {cls.AUTO_CONFIG_PATH}")
            else:
                # Eğer otomatik ayar dosyası yoksa, manuel'den kopyala
                cls._auto_data = copy.deepcopy(cls._manual_data)
                # Otomatik ayar dosyasından 'control_mode' anahtarını kaldırarak sorumlulukları ayır
                if 'control_mode' in cls._auto_data:
                    del cls._auto_data['control_mode']
                cls.save_auto_config()

        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"[CONFIG_LOAD_ERR] {e}")

    @classmethod
    def hot_reload_if_changed(cls):
        try:
            # DÜZELTME: Sadece manuel ayar dosyasının değişikliğini kontrol et
            if not os.path.exists(cls.MANUAL_CONFIG_PATH):
                return False
            st = os.stat(cls.MANUAL_CONFIG_PATH)
            if cls._last_mtime is None or st.st_mtime != cls._last_mtime:
                cls.load(force=True)
                return True
            return False
        except (IOError, OSError) as e:
            logging.error(f"[HOT_RELOAD_ERR] Dosya sistemi hatası: {e}")
            return False

    @classmethod
    def tf_profile(cls, timeframe: str, default=None) -> dict:
        """
        scans.tf_profiles.<tf> düğümünü döndürür (merged: manual + auto if control_mode=auto).
        """
        tf = str(timeframe or "").strip()
        return cls.get(f"scans.tf_profiles.{tf}", default or {})

    @classmethod
    def get(cls, path: str, default=None):
        """
         Konfigürasyon değerini alır.
         - Her zaman manuel ayarları temel alır.
         - Eğer kontrol modu 'auto' ise, otomatik ayarları manuel ayarların üzerine "bindirir".
           Bu, manuelde olup otomatikte olmayan bir ayarın yine de erişilebilir olmasını sağlar.
         - 'control_mode' sorgusu her zaman manuel dosyadan gelir.
        """
        # DÜZELTME: `RecursionError` hatasını çözmek için mantık yeniden yapılandırıldı.
        # `control_mode()` çağrısı artık `get` içinde yapılmıyor.

        # 1. Önce sadece manuel veriden `control_mode.active` değerini al.
        # Bu, `control_mode()`'un `get()`'i çağırmasından kaynaklanan sonsuz döngüyü kırar.
        if not cls._initialized:
            cls.init()

        active_mode = cls._manual_data.get("control_mode", {}).get("active", "manual")

        # 2. Veri kaynağını belirle ve birleştir.
        # DÜZELTME: `auto` modda, manuel ayarların üzerine otomatik ayarları bindir.
        # Bu, `auto`'da sadece değişen değerler olsa bile tam bir konfigürasyon sağlar.
        merged_data = copy.deepcopy(cls._manual_data)
        if active_mode == 'auto':
            def deep_update(target, source):
                """İç içe sözlükleri birleştiren yardımcı fonksiyon."""
                for key, value in source.items():
                    if isinstance(value, dict) and key in target and isinstance(target[key], dict):
                        deep_update(target[key], value)
                    else:
                        target[key] = value

            deep_update(merged_data, cls._auto_data)

        try:
            # 3. Birleştirilmiş veriden değeri al.
            cur = merged_data
            for p in path.split("."):
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    return default

            # ✅ NEW: Tuner formatı unwrap
            # Leaf node şu formdaysa: {"value": X, "bounds": [...]}, direkt X döndür.
            if isinstance(cur, dict) and "value" in cur and len(cur.keys()) <= 3:
                # bounds/step gibi ekstra alanlar olabilir; leaf ise value döndür
                try:
                    return cur.get("value", default)
                except Exception:
                    return default
            return cur
        except Exception as e:
            logging.error(f"Hata: {e}")
            return default

    @classmethod
    def get_manual(cls, path: str, default=None):
        """
        Sadece manuel ayar dosyasından (_manual_data) bir değer alır.
        Bu, 'auto' mod aktifken bile düzenlenebilir manuel ayarları göstermek için kullanılır.
        """
        try:
            cur = cls._manual_data
            for p in path.split("."):
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    return default
            return cur
        except Exception as e:
            logging.error(f"[CONFIG_GET_MANUAL_ERR] Hata: {e}")
            return default

    @classmethod
    def _get_from_dict(cls, data: dict, path: str, default=None):
        """Verilen bir sözlükten nokta notasyonlu yolu kullanarak değer alır."""
        try:
            cur = data
            for p in path.split("."):
                if isinstance(cur, dict) and p in cur:
                    cur = cur[p]
                else:
                    return default
            # Dönen değerin değiştirilmesini önlemek için bir kopya döndür
            return copy.deepcopy(cur)
        except Exception as e:
            logging.error(f"[CONFIG_GET_HELPER_ERR] Hata: {e}")
            return default


    @classmethod
    def set(cls, path: str, value):
        """
        Konfigürasyon değerini ayarlar ve **her zaman manuel ayar dosyasına** yazar.
        Bu metot, Telegram menüsünden yapılan kullanıcı değişiklikleri için tasarlanmıştır.
        StrategyAdaptiveTuner bu metodu kullanmaz.
        """
        # İYİLEŞTİRME: set_manual metodunu kullanarak kod tekrarını önle.
        return cls.set_manual(path, value)

    @classmethod
    def save(cls):
        """
        Değişiklik yapılan konfigürasyon dosyasını kaydeder.
        Bu genellikle 'control_mode' için manuel dosyayı kaydeder.
        """
        return cls.save_manual_config()

    @classmethod
    def save_manual_config(cls) -> bool:
        """
        Bellekteki manuel ayarları (_manual_data) dosyaya yazar.
        """
        try:
            os.makedirs(os.path.dirname(cls.MANUAL_CONFIG_PATH), exist_ok=True)
            with open(cls.MANUAL_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cls._manual_data, f, ensure_ascii=False, indent=2)
            logging.info("[CONFIG] Manuel ayarlar kaydedildi.")
            # Dosyaya yazdıktan sonra bellekteki durumu senkronize et
            cls.load(force=True)
            return True
        except Exception as e:
            logging.error(f"[CONFIG_MANUAL_SAVE_ERR] {e}", exc_info=True)
            return False

    @classmethod
    def save_auto_config(cls):
        try:
            os.makedirs(os.path.dirname(cls.AUTO_CONFIG_PATH), exist_ok=True)
            tmp = cls.AUTO_CONFIG_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cls._auto_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cls.AUTO_CONFIG_PATH)
            logging.info("[CONFIG] Otomatik ayarlar kaydedildi.")
            return True
        except Exception as e:
            logging.error(f"[CONFIG_AUTO_SAVE_ERR] {e}", exc_info=True)
            return False

    @classmethod
    def set_auto(cls, key_path: str, value: Any):
        """
        Sadece otomatik ayar verisini (_auto_data) bellekte günceller.
        Bu metot, StrategyAdaptiveTuner tarafından kullanılır. Değişiklikleri
        kalıcı hale getirmek için `save_auto_config()` çağrılmalıdır.
        """
        try:
            keys = key_path.split('.')
            d = cls._auto_data

            # Son anahtar hariç tüm yolu dolaş, eksik sözlükleri oluştur
            for key in keys[:-1]:
                d = d.setdefault(key, {})

            # Son anahtara değeri ata
            d[keys[-1]] = value
            return True
        except (AttributeError, TypeError) as e:
            logging.error(f"[CONFIG_SET_AUTO_ERR] Geçersiz yol veya veri yapısı: {key_path} | Hata: {e}")
            return False

    @classmethod
    def set_manual(cls, key_path: str, value: any):
        """Her zaman manuel konfigürasyon verisi (_manual_data) üzerinde değişiklik yapar ve kaydeder.
        Her zaman manuel konfigürasyon dosyası (_manual_data) üzerinde değişiklik yapar.
        Bu metot, admin panelinden yapılan kullanıcı değişiklikleri için tasarlanmıştır.
        """
        try:
            keys = key_path.split('.')
            d = cls._manual_data

            # Son anahtar hariç tüm yolu dolaş, eksik sözlükleri oluştur
            for key in keys[:-1]:
                d = d.setdefault(key, {})

            # Son anahtara değeri ata
            d[keys[-1]] = value
            logging.info(f"[CONFIG_SET_MANUAL] Manuel ayar güncellendi: {key_path} = {value}")
            # Değişikliği kalıcı hale getirmek için kaydet
            cls.save_manual_config()
            return True

        except (AttributeError, TypeError) as e:
            logging.error(f"[CONFIG_SET_MANUAL_ERR] Geçersiz yol veya veri yapısı: {key_path} | Hata: {e}")
            return False

    # Kısa yardımcılar
    @classmethod
    def control_mode(cls):
        # Direct access to break recursion
        return cls._manual_data.get("control_mode", {}).get("active", "manual")

    @classmethod
    def ai_settings(cls, tf: str):
        prof = cls.tf_profile(tf, {})  # ✅ üstteki (default parametreli) tf_profile kullanılır
        return (prof.get("ai_scan") or {}) if isinstance(prof, dict) else {}

    @classmethod
    def strat_settings(cls, tf: str):
        prof = cls.tf_profile(tf, {})  # ✅ üstteki (default parametreli) tf_profile kullanılır
        return (prof.get("strategy_scan") or {}) if isinstance(prof, dict) else {}

    @classmethod
    def charts(cls):
        return cls.get("charts", {}) or {}

    @classmethod
    def logging_conf(cls):
        return cls.get("logging", {}) or {}

    @classmethod
    def reporting(cls):
        return cls.get("reporting", {}) or {}

    @classmethod
    def risk(cls):
        return cls.get("risk", {}) or {}

    @classmethod
    def exchange_profile(cls, name: str):
        return cls.get(f"exchange_profiles.{name}", {}) or {}

# ==============================================================================
