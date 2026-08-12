# email_checker.py dosyamız burdan başlamaktadır
import ssl
from imapclient import IMAPClient
import email as email_module
import re
import asyncio
from typing import Dict, Optional, Any, List
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
from logger_config import *
from data.olimpos_data import db_operation
from signal_merkezi import parse_signal

logger = setup_logging('email_checker')
# logger.info("email_checker başlatıldı.")

# Sabit IMAP Port
IMAP_PORT = 993


def handle_exceptions(func):
    """
    Genel hata yakalama ve loglama decorator'ı
    """
    @wraps(func)
    async def wrapper(*args: Any, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"{func.__name__} metodunda hata: {e}")
            return None
    return wrapper


class EmailChecker:
    def __init__(self, telegram_bot: Bot):
        # İlk seviye indentasyon
        if not telegram_bot:
            raise ValueError("Telegram bot gereklidir")

        # Aynı seviye indentasyon
        self._stop_event = asyncio.Event()
        self.telegram_bot = telegram_bot
        self.imap_clients: Dict[str, IMAPClient] = {}

    @staticmethod
    @handle_exceptions
    async def get_unique_email_settings(
    ) -> List[Dict[str, str]]:
        # Metod seviyesi indentasyon
        query = """
        SELECT DISTINCT 
            email_username, 
            email_password, 
            imap_server
        FROM telegram_notification_channels 
        WHERE 
            email_username IS NOT NULL AND 
            email_password IS NOT NULL AND 
            imap_server IS NOT NULL AND
            aktif_pasif = 'Aktif'
        """
        results = await asyncio.to_thread(
            db_operation,
            query,
            params={},  # Boş params ekleyin
            operation='select',
            fetch='all',
            fetch_all=True  # fetch_all parametresini ekleyin
        )

        return [
            {
                'email': result[0],
                'password': result[1],
                'imap_server': result[2]
            }
            for result in results if len(result) >= 3
        ]

    @handle_exceptions
    async def create_and_maintain_imap_client(
            self,
            email_settings: Dict[str, str],
    ) -> Optional[IMAPClient]:
        """
        Güvenli ve sürekli IMAP istemcisi oluştur ve bakımını yap
        """
        email_username = email_settings['email']

        # Eğer zaten bir bağlantı varsa kullan
        if email_username in self.imap_clients:
            return self.imap_clients[email_username]

        # SSL Context oluştur
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        try:
            # IMAP bağlantısı
            client = IMAPClient(
                email_settings['imap_server'],
                use_uid=True,
                ssl=True,
                ssl_context=ssl_context,
                port=IMAP_PORT
            )

            # Login işlemi
            client.login(
                email_settings['email'],
                email_settings['password']
            )

            logger.info(f"IMAP bağlantısı başarılı: {email_username}")

            # Bağlantıyı sakla
            self.imap_clients[email_username] = client

            return client

        except Exception as e:
            logger.error(f"IMAP bağlantı hatası: {email_username} - {e}")
            if email_username in self.imap_clients:
                del self.imap_clients[email_username]
            return None

    @handle_exceptions
    async def check_emails(
            self,
            client: IMAPClient,
            email_settings: Dict[str, str]
    ):
        """
        Email kontrolü ve işlenmesi
        """
        try:
            # Klasörü seç
            client.select_folder('INBOX')

            # Tarih formatını düzelt
            from_date = (datetime.now() - timedelta(hours=24)).strftime('%d-%b-%Y')

            # Arama kriterlerini güncelle
            search_criteria = [
                'SINCE', from_date,
                'UNSEEN'  # Sadece okunmamış mailleri al
            ]

            try:
                message_ids = await asyncio.to_thread(
                    client.search,
                    search_criteria,
                    charset='UTF-8'
                )

                logger.info(f"{email_settings['email']} için bulunan toplam email sayısı: {len(message_ids)}")

                # Filtrelenmiş mesaj IDleri
                filtered_message_ids = []

                # Her mail için gönderen ve konu kontrolü
                for msg_id in message_ids:
                    # Mesaj detaylarını çek
                    message_data = await asyncio.to_thread(
                        client.fetch,
                        [msg_id],
                        ['ENVELOPE'],
                        modifiers=None
                    )

                    envelope = message_data[msg_id][b'ENVELOPE']
                    sender = envelope.from_[0]
                    sender_email = sender.mailbox.decode('utf-8') + '@' + sender.host.decode('utf-8')
                    subject = envelope.subject.decode('utf-8', errors='ignore')

                    # Kesin kontrol:
                    # 1. Gönderen noreply@tradingview.com
                    # 2. Konu Alarm: Olimpos_ ile başlamalı
                    if (sender_email == 'noreply@tradingview.com' and
                            subject.startswith('Alarm: Olimpos_')):
                        filtered_message_ids.append(msg_id)
                    # Diğer mailleri hiçbir işleme tabi tutma, yok say

                logger.info(f"{email_settings['email']} için uygun email sayısı: {len(filtered_message_ids)}")

                # Uygun mesajları işle
                for msg_id in filtered_message_ids:
                    await self._process_email_message(client, msg_id, email_settings['email'])

            except Exception as e:
                logger.error(f"{email_settings['email']} email kontrol hatası: {e}")

        except Exception as e:
            logger.error(f"Klasör seçme hatası: {e}")

    @staticmethod
    def _extract_trading_view_details(html_content):
        """
        TradingView email içeriğinden detaylı bilgileri çıkarır
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            full_text = soup.get_text(separator=' ', strip=True)

            # İki kez geçen "alarmınız çalıştı" ifadesini bulup sonrasını al
            alarm_matches = list(re.finditer(r'alarmınız\s*çalıştı', full_text, re.IGNORECASE))

            # Sembol tespiti için daha esnek bir yaklaşım
            symbol_match = re.search(r'(?:📉\s*Sembol:\s*|^)([A-Z]+(?:/[A-Z]+)?(?:\.P)?)', full_text, re.IGNORECASE)
            symbol = symbol_match.group(1) if symbol_match else 'Bilinmeyen Sembol'

            # "Grafiğinizi açın" ifadesini bulma
            grafik_match = re.search(r'Grafiğinizi\s*açın', full_text, re.IGNORECASE)

            # Detaylı metni belirleme
            if len(alarm_matches) >= 2 and grafik_match:
                # İkinci "alarmınız çalıştı" ile "Grafiğinizi açın" arasındaki metni al
                start_index = alarm_matches[1].end()
                end_index = grafik_match.start()
                detailed_text = full_text[start_index:end_index].strip()
            elif alarm_matches and grafik_match:
                # İlk "alarmınız çalıştı" ile "Grafiğinizi açın" arasındaki metni al
                start_index = alarm_matches[0].end()
                end_index = grafik_match.start()
                detailed_text = full_text[start_index:end_index].strip()
            else:
                # Eğer özel aralık bulunamazsa, tüm metni kullan
                detailed_text = full_text

            # Pozisyon türünü ve durumunu tespit etme
            long_open_match = re.search(r'🟢\s*LONG\s*Pozisyon\s*Açıldı', detailed_text, re.IGNORECASE)
            short_open_match = re.search(r'🔴\s*SHORT\s*Pozisyon\s*Açıldı', detailed_text, re.IGNORECASE)
            long_close_match = re.search(r'🟢\s*LONG\s*Pozisyon\s*Kapatıldı', detailed_text, re.IGNORECASE)
            short_close_match = re.search(r'🔴\s*SHORT\s*Pozisyon\s*Kapatıldı', detailed_text, re.IGNORECASE)

            # Pozisyon açılış durumu
            if long_open_match or short_open_match:
                # Giriş fiyatları ve Stop Loss için regex
                entry_match = re.search(r'💰\s*Fiyat\s*1\s*[=:]\s*([\d.]+)', detailed_text, re.IGNORECASE)
                fiyat2_match = re.search(r'💰\s*Fiyat\s*2\s*[=:]\s*([\d.]+)', detailed_text, re.IGNORECASE)
                stop_loss_match = re.search(r'🚨\s*Stop\s*Loss\s*[=:]\s*([\d.]+)', detailed_text, re.IGNORECASE)

                # Hedef fiyatları çıkar
                target_matches = re.findall(r'(?:1️⃣|2️⃣|3️⃣|4️⃣|5️⃣)\s*Hedef\s*\d*\s*[=:]\s*([\d.]+)', detailed_text,
                                            re.IGNORECASE)

                result = {
                    'signal_type': 'OPEN',
                    'position_type': 'LONG' if long_open_match else 'SHORT',
                    'symbol': symbol,
                    'entry_price1': float(entry_match.group(1)) if entry_match else None,
                    'entry_price2': float(fiyat2_match.group(1)) if fiyat2_match else None,
                    'stop_loss': float(stop_loss_match.group(1)) if stop_loss_match else None,
                    'targets': [float(target) for target in target_matches] if target_matches else [],
                    'full_text': detailed_text,
                    'html_content': html_content
                }

            # Pozisyon kapanış durumu
            elif long_close_match or short_close_match:
                # Kapanış fiyatını çıkar
                close_price_match = re.search(r'💰\s*Kapanan\s*Fiyat\s*[=:]\s*([\d.]+)', detailed_text, re.IGNORECASE)

                result = {
                    'signal_type': 'CLOSE',
                    'position_type': 'LONG' if long_close_match else 'SHORT',
                    'symbol': symbol,
                    'close_price': float(close_price_match.group(1)) if close_price_match else None,
                    'full_text': detailed_text,
                    'html_content': html_content
                }

            else:
                # Tanınmayan sinyal
                result = {
                    'signal_type': 'UNKNOWN',
                    'symbol': symbol,
                    'full_text': detailed_text,
                    'html_content': html_content
                }

            # Detaylı log kaydı
            logger.info(f"📦 Çıkarılan Detaylar: {result}")

            return result

        except Exception as error:
            # Detaylı hata log kaydı
            logger.error(f"❌ Email detayları çıkarılırken hata: {error}")
            logger.exception("🔍 Hata Detayları:")

            # Hata anında varsayılan bir sözlük döndür
            return {
                'signal_type': 'ERROR',
                'symbol': 'Bilinmeyen',
                'full_text': '',
                'html_content': ''
            }

    @handle_exceptions
    async def _process_email_message(
            self,
            imap_client: IMAPClient,
            msg_id,
            email_username,
    ):
        """
        Tek bir email mesajını işleme
        """
        logger.info(f"📧 Email işleme başladı - Mesaj ID: {msg_id}, Kullanıcı: {email_username}")

        try:
            # 1. Email içeriğini çekme adımı
            # logger.info("🔍 Email içeriği çekiliyor...")
            raw_message = await asyncio.to_thread(
                imap_client.fetch,
                [msg_id],
                ['RFC822', 'UID'],
                modifiers=None
            )
            if not raw_message:
                logger.warning(f"⚠️ Raw message boş - Mesaj ID: {msg_id}")
                return
            # 2. Email mesajını parse etme
            # logger.info("🧩 Email mesajı parse ediliyor...")
            email_message = email_module.message_from_bytes(
                raw_message[msg_id][b'RFC822']
            )
            # 3. Email gönderen adresini al
            # logger.info("👤 Gönderen email adresi çıkarılıyor...")
            sender_email = self._extract_sender_email(email_message)
            if not sender_email:
                logger.warning("⚠️ Gönderen email adresi bulunamadı")
                return
            # 4. Gönderen email kontrolü
            if sender_email == 'noreply@tradingview.com':
                # logger.info("🔄 TradingView noreply adresi tespit edildi, kullanıcı email'i kullanılacak")
                sender_email = email_username
            # logger.info(f"📬 Gönderen Email Adresi: {sender_email}")
            # 5. HTML içeriği çıkarma
            # logger.info("🌐 HTML içeriği çıkarılıyor...")
            html_content = self._extract_html_content(email_message)
            if not html_content:
                logger.warning("⚠️ HTML içeriği boş")
                return
            # 6. Detaylı bilgileri çıkarma
            # logger.info("🕵️ Trading detayları çıkarılıyor...")
            trading_details = self._extract_trading_view_details(html_content)
            # Detaylı trading detayları loglaması
            # logger.info(f"🔍 Çıkarılan Trading Detayları: {json.dumps(trading_details, indent=2)}")
            if not trading_details or trading_details.get('signal_type') in ['UNKNOWN', 'ERROR']:
                logger.warning("⚠️ Trading detayları çıkarılamadı veya bilinmeyen sinyal")
                return
            # 7. Link çıkarma
            # logger.info("🔗 Trading linki çıkarılıyor...")
            trading_link = self._extract_trading_link_with_button_text(html_content)
            if not trading_link:
                logger.warning("⚠️ Trading linki bulunamadı")
                return
            # logger.info(f"🌐 Trading Linki Bulundu: {trading_link}")
            # 8. Kullanıcı kanallarını alma
            logger.info("📡 Kullanıcı aktif kanalları kontrol ediliyor...")
            try:
                channels = await self.get_active_channels(sender_email)
                if not channels:
                    logger.warning(f"⚠️ Gönderen email için aktif kanal bulunamadı: {sender_email}")
                    return
                logger.info(f"📡 Aktif Kanallar: {channels}")
            except Exception as channel_error:
                logger.error(f"❌ Kanal bilgileri alınırken hata: {channel_error}")
                return
            # 9. Telegram kanallarına gönderme
            # logger.info("📤 Telegram kanallarına gönderim yapılıyor...")
            try:
                await self.send_to_telegram_channels(
                    trading_link,
                    trading_details,
                    sender_email
                )
            except Exception as send_error:
                logger.error(f"❌ Telegram kanallarına gönderim hatası: {send_error}")
                return
            # 10. Sinyal metnini oluşturma
            # logger.info("📝 Sinyal metni oluşturuluyor...")

            def create_signal_text_local(details):
                try:
                    symbol = details.get('symbol', 'Bilinmeyen').replace('.P', '')
                    position_type = details.get('position_type', 'Bilinmeyen')
                    signal_type = details.get('signal_type', 'Bilinmeyen')

                    # logger.info(f"📊 Sinyal Detayları Özeti:")
                    # logger.info(f"🏷️ Signal Type: {signal_type}")
                    # logger.info(f"📈 Sembol: {symbol}")
                    # logger.info(f"🔀 Pozisyon Tipi: {position_type}")

                    if signal_type == 'CLOSE':
                        logger.info("🔒 Kapanış sinyali tespit edildi")
                        close_price = details.get('close_price')
                        signal_text_local = f"""
                    Signal Type: {signal_type}    
                    Sembol: {symbol}
                    Pozisyon Tipi: {position_type}
                    Kapanış Fiyatı: {close_price if close_price else 'Bulunamadı'}
                        """.strip()

                    else:
                        # logger.info("🚀 Açılış sinyali tespit edildi")
                        entry_points = [
                            details.get('entry_price1'),
                            details.get('entry_price2')
                        ]
                        entry_points = [ep for ep in entry_points if ep is not None]

                        stop_loss = details.get('stop_loss')
                        targets = details.get('targets', [])

                        # Detaylı giriş noktaları ve hedef loglaması
                        # logger.info(f"🎯 Giriş Fiyatları: {entry_points}")
                        # logger.info(f"⛔ Stop Loss: {stop_loss}")
                        # logger.info(f"🎳 Hedef Fiyatlar: {targets}")

                        signal_text_local = (
                            f"""
Signal Type: {signal_type}
Sembol: {symbol}
Pozisyon Tipi: {position_type}
Giriş Fiyatları: {', '.join(map(str, entry_points)) if entry_points else 'Bulunamadı'}
Stop Loss: {stop_loss if stop_loss is not None else 'Belirtilmedi'}
Hedef Fiyatlar: {', '.join(map(str, targets)) if targets else 'Bulunamadı'}
                            """.strip())

                    # Oluşturulan sinyal metnini logla
                    # logger.info("📋 Oluşturulan Sinyal Metni:")
                    # logger.info(f"```\n{signal_text_local}\n```")

                    return signal_text_local

                except Exception as error:
                    logger.error(f"❌ Sinyal metni oluşturma hatası: {error}")
                    return None
            # 11. Sinyal metnini oluştur
            # logger.info("📋 Sinyal metni hazırlanıyor...")
            local_signal_text = create_signal_text_local(trading_details)

            # 12. Sinyal parse işlemi
            if local_signal_text:
                try:
                    # Parse edilecek metni düzenle
                    parse_text = f"""
            SİNYAL TİPİ: {trading_details.get('signal_type', 'UNKNOWN')}
            Sembol: {trading_details.get('symbol', 'Bilinmeyen').replace('.P', '')}
            Pozisyon Tipi: {trading_details.get('position_type', 'Bilinmeyen')}
            """

                    # Açılış sinyali için
                    if trading_details.get('signal_type') == 'OPEN':
                        parse_text = f"""
SİNYAL TİPİ: {trading_details.get('signal_type', 'UNKNOWN')}
Sembol: {trading_details.get('symbol', 'Bilinmeyen').replace('.P', '')}
Pozisyon Tipi: {trading_details.get('position_type', 'Bilinmeyen')}
Giriş Fiyatları:{trading_details.get('entry_price1', 'Belirtilmedi')} / {trading_details.get('entry_price2', 
                                                                                             'Belirtilmedi')}
Stop Loss: {trading_details.get('stop_loss', 'Belirtilmedi')}
Hedef Fiyatlar: {', '.join(map(str, trading_details.get('targets', []))) if trading_details.get('targets') else 
                        'Bulunamadı'}
                        """

                    # Kapanış sinyali için
                    elif trading_details.get('signal_type') == 'CLOSE':
                        parse_text = f"""
SİNYAL TİPİ: {trading_details.get('signal_type', 'UNKNOWN')}
Sembol: {trading_details.get('symbol', 'Bilinmeyen').replace('.P', '')}
Pozisyon Tipi: {trading_details.get('position_type', 'Bilinmeyen')}
Kapanış Fiyatı: {trading_details.get('close_price', 'Belirtilmedi')}
                        """

                    # Parse sonucunu logla

                    parse_result = await parse_signal(
                        parse_text,
                        sender_email=sender_email,
                        active_channels=channels
                    )

                    # Sonucu kontrol etme
                    if parse_result:
                        # Parse başarılıysa işlemler
                        logger.info(f"Parse sonucu: {parse_result}")
                    else:
                        # Parse başarısızsa
                        logger.warning("Signal parse edilemedi")

                    # logger.info(f"✅ Parse Sonucu: {parse_result}")
                except Exception as parse_error:
                    logger.error(f"❌ Sinyal parse hatası: {parse_error}")

            # 13. Mesajı okundu olarak işaretleme
            # logger.info("✅ Mesaj okundu olarak işaretlenecek...")
            try:
                await asyncio.to_thread(
                    imap_client.add_flags,
                    [msg_id],
                    ['\\Seen'],
                    silent=False
                )
                logger.info("✅ Mesaj başarıyla okundu olarak işaretlendi")
            except Exception as mark_error:
                logger.error(f"❌ Mesaj işaretleme hatası: {mark_error}")

        except Exception as general_error:
            logger.error(f"❌ Email işleme genel hatası: {general_error}")

    @staticmethod
    def _extract_sender_email(email_message):
        """
        Email gönderen adresini çıkarma metodu
        """
        try:
            # Farklı email formatlarını destekleyen çıkarma işlemi
            from_header = email_message.get('From', '')

            # Regex ile email adresi çıkarma
            email_pattern = r'[\w\.-]+@[\w\.-]+'
            match = re.search(email_pattern, from_header)

            if match:
                return match.group(0)

            # Eğer regex başarısız olursa
            if '<' in from_header and '>' in from_header:
                return from_header.split('<')[-1].split('>')[0].strip()

            return from_header.strip()

        except Exception as e:
            logger.error(f"Email adresi çıkarma hatası: {e}")
            return ''

    @handle_exceptions
    async def run(self):
        """
        Email kontrol mekanizması (sürekli bağlantı ve anlık bildirim)
        """
        logger.info("Email kontrol mekanizması başlatılıyor")
        email_settings_list = await self.get_unique_email_settings()

        if not email_settings_list:
            logger.error("İşlenecek email ayarı bulunamadı")
            return

        # Her email hesabı için ayrı bir izleme görevi oluştur
        tasks = [
            asyncio.create_task(self.monitor_email_idle(settings))
            for settings in email_settings_list
        ]

        await asyncio.gather(*tasks)

    @handle_exceptions
    async def monitor_email_idle(self, email_settings: Dict[str, str]):
        """
        IMAP IDLE modunda sürekli email izleme
        """
        while True:
            try:
                # Sürekli IMAP bağlantısı kur
                client = await self.create_and_maintain_imap_client(email_settings)

                if client is None:
                    logger.error(f"{email_settings['email']} için IMAP bağlantısı kurulamadı")
                    await asyncio.sleep(30)  # Bağlantı hatası durumunda bekle
                    continue

                # INBOX klasörünü seç
                client.select_folder('INBOX')

                logger.info(f"{email_settings['email']} için IDLE modu başlatılıyor")

                # IDLE modunda bekle
                while True:
                    try:
                        # Yeni gelen mesajları kontrol et
                        # timeout parametresini kaldır
                        await asyncio.to_thread(
                            client.idle
                        )

                        # Sunucudan yanıt bekle (1 dakika)
                        await asyncio.sleep(60)

                        # IDLE durumunu sonlandır
                        await asyncio.to_thread(client.idle_done)

                        # Yeni mesaj kontrolü
                        await self.check_new_emails(client, email_settings)

                        # Tekrar IDLE moduna geç
                        client.select_folder('INBOX')

                    except Exception as idle_error:
                        logger.error(f"IDLE modunda hata: {idle_error}")
                        break

            except Exception as e:
                logger.error(f"Email izleme hatası: {e}")
                await asyncio.sleep(30)  # Hata durumunda bekle

    @handle_exceptions
    async def check_new_emails(self, client: IMAPClient, email_settings: Dict[str, str]):
        """
        Yeni gelen emailları kontrol et ve işle
        """
        try:
            # Son gelen emaili bul
            search_criteria = ['UNSEEN']  # Okunmamış mailleri kontrol et

            message_ids = await asyncio.to_thread(
                client.search,
                search_criteria,
                charset='UTF-8'
            )
            # e mailleri kontrol etmek için daha sonra mutlaka açmalıyım
            # logger.info(f"{email_settings['email']} için yeni email sayısı: {len(message_ids)}")

            # Her yeni email için işleme
            for msg_id in message_ids:
                # Mesaj detaylarını çek
                message_data = await asyncio.to_thread(
                    client.fetch,
                    [msg_id],
                    ['ENVELOPE'],
                    modifiers=None
                )

                envelope = message_data[msg_id][b'ENVELOPE']
                sender = envelope.from_[0]
                sender_email = sender.mailbox.decode('utf-8') + '@' + sender.host.decode('utf-8')
                subject = envelope.subject.decode('utf-8', errors='ignore')

                # Kesin kontrol:
                # 1. Gönderen noreply@tradingview.com
                # 2. Konu Alarm: Olimpos_ ile başlamalı
                if (sender_email == 'noreply@tradingview.com' and
                        subject.startswith('Alarm: Olimpos_')):
                    # Kriterlere uyan mail için işleme devam et
                    await self._process_email_message(client, msg_id, email_settings['email'])
                # Diğer mailleri hiçbir işleme tabi tutma, yok say

        except Exception as e:
            logger.error(f"{email_settings['email']} yeni email kontrol hatası: {e}")

    @staticmethod
    async def process_trade_signal(user_id=None, signal_text=None):
        try:
            # Detaylı log ekleyin
            logger.info(f"Trade sinyali işleniyor - Kullanıcı ID: {user_id}")
            logger.info(f"Sinyal metni: {signal_text}")

            # Sinyal metni boş mu kontrol et
            if not signal_text:
                logger.warning("Sinyal metni boş")
                return False

            # Gerekli kontrolleri yapın
            if not user_id:
                logger.error("Kullanıcı ID si gereklidir")
                return False

            # Sinyal işleme mantığınızı buraya ekleyin
            # Örnek:
            signal_processed = True  # Gerçek işleme sonucuna göre değişecek

            return signal_processed

        except Exception as e:
            logger.error(f"Trade sinyali işleme hatası: {e}", exc_info=True)
            return False

    @staticmethod
    def _extract_html_content(email_message):
        """
        Email HTML içeriğini çıkarma
        """
        try:
            if email_message.is_multipart():
                for part in email_message.walk():
                    if part.get_content_type() == 'text/html':
                        return part.get_payload(decode=True).decode()
            else:
                return email_message.get_payload(decode=True).decode()
        except Exception as e:
            logger.error(f"HTML içerik çıkarma hatası: {e}")
            return ""

    @staticmethod
    def _extract_trading_link_with_button_text(html_content):
        """
        Özel regex ile 'Grafiğinizi Açın' butonundaki linki çıkar
        """
        try:
            # Daha esnek regex deseni
            pattern = r'<a\s+[^>]*href="(https://[^"]+)"[^>]*>(?:Grafiğinizi\s*açın|View\s*Chart)</a>'
            match = re.search(pattern, html_content, re.IGNORECASE | re.UNICODE)

            return match.group(1) if match else None
        except Exception as e:
            logger.error(f"Link çıkarma hatası: {e}")
            return None

    @handle_exceptions
    async def get_active_channels(self, email, exchange=None):
        """
        Aktif kanalları yeni tablodan çek
        """
        try:
            query = """
            SELECT 
                channel_id,
                user_id,
                exchange,
                username,
                channel_name,
                admin_level
            FROM 
                telegram_notification_channels
            WHERE 
                aktif_pasif = 'Aktif' AND 
                email_username = :email AND
                (admin_level = 0 OR admin_level = 1)
                {exchange_condition}
            """

            params = {'email': email}

            # Opsiyonel exchange filtresi
            if exchange:
                query = query.format(exchange_condition=' AND exchange = :exchange')
                params['exchange'] = exchange
            else:
                query = query.format(exchange_condition='')

            # Asenkron db_operation kullanımı
            results = await asyncio.to_thread(
                db_operation,
                query,
                params=params,
                operation='select',
                fetch='all',
                fetch_all=True  # fetch_all parametresini ekleyin
            )

            # Sonuçları dictionary formatında döndür
            channels = [
                {
                    'channel_id': row[0],
                    'user_id': row[1],
                    'exchange': row[2],
                    'username': row[3],
                    'channel_name': row[4],
                    'admin_level': row[5]
                }
                for row in results
            ]

            return channels

        except Exception as e:
            logger.error(f"Admin Channel ID alma hatası - Email: {email}, Hata: {e}")
            return []

    async def send_to_telegram_channels(self, trading_link: str, trading_details: dict, sender_email: str):
        """
        Telegram bildirim kanallarına detaylı mesaj gönderme
        """
        query = """
        SELECT 
            channel_id, 
            username, 
            channel_name, 
            exchange, 
            admin_level
        FROM 
            telegram_notification_channels
        WHERE 
            aktif_pasif = 'Aktif' AND 
            (admin_level = 0 OR admin_level = 1) AND
            email_username = :email
        """

        try:
            results = await asyncio.to_thread(
                db_operation,
                query,
                params={'email': sender_email},
                operation='select',
                fetch='all',
                fetch_all=True  # Parametreyi ekleyin
            )

            async def send_channel_message(channel_info: tuple):
                channel_id, username, channel_name, exchange, admin_level = channel_info[:5]

                try:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("Grafiğinizi Açın", url=trading_link)]
                    ])

                    # Detaylı mesaj oluşturma
                    message_text = (
                        f"🚨 Olimpos Alarm Bildirimi\n"
                        f"Kullanıcı Adı: {username}\n"
                        f"Kanal Adı: {channel_name}\n"
                        f"Sinyal Tipi: {trading_details.get('signal_type', 'Bilinmeyen')}\n"
                        f"\n{trading_details.get('full_text', 'Detay bulunamadı.').split('Grafiğinizi açın')[0]}"
                    )
                    await self.telegram_bot.send_message(
                        chat_id=channel_id,
                        text=message_text,
                        reply_markup=keyboard
                    )

                    logger.info(f"Mesaj gönderildi - Kanal ID: {channel_id}")

                except Exception as channel_error:
                    logger.error(f"Kanala mesaj gönderme hatası: {channel_error}")

            # Eş zamanlı mesaj gönderme
            await asyncio.gather(
                *[send_channel_message(channel_info) for channel_info in results]
            )

        except Exception as e:
            logger.error(f"Telegram kanallarına toplu gönderim hatası: {e}")
            logger.exception("Detaylı Gönderim Hatası:")


def track_performance(func):
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            logger.info(f"{func.__name__} çalışma süresi: {time.time() - start_time} saniye")
            return result
        except Exception as e:
            logger.error(f"{func.__name__} metodunda hata: {e}")
            raise
    return wrapper
