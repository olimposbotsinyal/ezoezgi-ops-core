import logging
import os
import sys
import tempfile
import time
import shutil
from logging.handlers import RotatingFileHandler
from functools import wraps


def safe_log_rotation(file_path):
    """
    Log dosyası döndürme işlemi için güvenli mekanizma

    Args:
        file_path (str): Log dosyası yolu
    """
    try:
        # Log dosyasını geçici bir konuma taşı
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, f'log_rotate_{os.path.basename(file_path)}')

        # Dosya meşgulse bekle
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                # Dosyayı kopyala
                shutil.copy2(file_path, temp_file)

                # Orijinal dosyayı temizle
                with open(file_path, 'w'):
                    pass

                break
            except PermissionError:
                # Kısa bir süre bekle
                time.sleep(0.5)
        else:
            print(f"Log dosyası döndürme işlemi başarısız: {file_path}")
    except Exception as e:
        print(f"Log döndürme hatası: {e}")


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    Güvenli log döndürme için özel handler
    """

    def doRollover(self):
        """
        Dosya döndürme işlemini güvenli bir şekilde gerçekleştir
        """
        if self.stream:
            self.stream.close()
            self.stream = None

        # Log dosyası döndürme işlemi
        safe_log_rotation(self.baseFilename)

        # Yeni log dosyası oluştur
        if not self.delay:
            self.stream = self._open()


def setup_logging(
        logger_name='olimpos_admin_logları',
        log_level=logging.INFO,
        log_dir=None,
        max_bytes=10 * 1024 * 1024,  # 10 MB
        backup_count=5
):
    """
    Gelişmiş log kurulum fonksiyonu

    Args:
        logger_name (str): Logger adı
        log_level (logging level): Log seviyesi
        log_dir (str, optional): Log dizini
        max_bytes (int): Maksimum log dosyası boyutu
        backup_count (int): Yedek log dosyası sayısı

    Returns:
        logging.Logger: Yapılandırılmış logger
    """
    # Logger oluştur
    logger = logging.getLogger(logger_name)

    # Eğer logger zaten yapılandırılmışsa, mevcut logger'ı döndür
    if logger.handlers:
        return logger

    # Log dizini ayarları
    if log_dir is None:
        log_dir = os.path.join(os.getcwd(), 'logs', 'admin')

    os.makedirs(log_dir, exist_ok=True)

    # Log dosyası yolu
    log_file_path = os.path.join(log_dir, f'{logger_name}.log')

    # Mevcut handlerları temizle
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    # Log seviyesini ayarla
    logger.setLevel(log_level)

    # Güvenli dönen log dosyası handler'ı
    file_handler = SafeRotatingFileHandler(
        log_file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)

    # Console handler'ı
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Handler'ları logger'a ekle
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Propagation'ı kapat
    logger.propagate = False

    return logger


# Özel logger'lar
def get_admin_logger():
    return setup_logging('olimpos_admin_logları')


def get_user_management_logger():
    return setup_logging('olimpos_user_management_logları')


def get_channel_logger():
    return setup_logging('olimpos_channel_logları')


def get_api_logger():
    return setup_logging('olimpos_api_logları')


def get_error_logger():
    return setup_logging('olimpos_kritik_hata_logları', log_level=logging.ERROR)


class LogController:
    _log_controllers = {}  # Farklı loggerlar için kontrol mekanizması

    @classmethod
    def disable_logs(cls, logger_name='olimpos_admin_logları'):
        """Belirli bir logger için logları devre dışı bırak"""
        if logger_name not in cls._log_controllers:
            cls._log_controllers[logger_name] = {
                'enabled': True,
                'original_level': logging.INFO
            }

        logger = logging.getLogger(logger_name)
        cls._log_controllers[logger_name]['original_level'] = logger.level
        logger.setLevel(logging.CRITICAL)  # Sadece kritik hataları kaydet
        cls._log_controllers[logger_name]['enabled'] = False
        print(f"{logger_name} için loglar kapatıldı.")

    @classmethod
    def enable_logs(cls, logger_name='olimpos_admin_logları'):
        """Belirli bir logger için logları yeniden etkinleştir"""
        if logger_name in cls._log_controllers:
            logger = logging.getLogger(logger_name)
            original_level = cls._log_controllers[logger_name]['original_level']
            logger.setLevel(original_level)
            cls._log_controllers[logger_name]['enabled'] = True
            print(f"{logger_name} için loglar yeniden etkinleştirildi.")
        else:
            print(f"{logger_name} için önceden kapatılmış bir log bulunamadı.")

    @classmethod
    def is_logging_enabled(cls, logger_name='olimpos_admin_logları'):
        """Log durumunu kontrol et"""
        return cls._log_controllers.get(logger_name, {}).get('enabled', True)

    @classmethod
    def get_all_loggers(cls):
        """Tüm logger adlarını listele"""
        return [
            'olimpos_user_management_logları',
            'olimpos_api_logları',
            'olimpos_kritik_hata_logları'
        ]


def log_control(logger_name='olimpos_admin_logları'):
    """
    Log kontrolü için decorator
    Belirli bir logger için log kontrolü sağlar
    """
    def decorator(func):
        @wraps(func)  # wraps doğru şekilde kullanıldı
        async def wrapper(*args, **kwargs):
            # Log durumunu kontrol et
            if not LogController.is_logging_enabled(logger_name):
                # Loglar kapalıysa, sessiz bir şekilde çalış
                return await func(*args, **kwargs)

            # Loglar açıksa normal şekilde çalış
            return await func(*args, **kwargs)

        return wrapper

    return decorator
