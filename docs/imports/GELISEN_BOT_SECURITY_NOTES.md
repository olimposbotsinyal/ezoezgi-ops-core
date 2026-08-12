# Gelisen_Bot — Güvenlik Notları (Secret Leakage Taraması)

> Kapsam: Dosya adı + içerik pattern düzeyinde temel tarama (statik grep, hiçbir kod
> çalıştırılmadı). Bu bir penetrasyon testi veya kapsamlı secret-scanning aracı
> (gitleaks/trufflehog vb.) çalıştırması değildir — temel/gözle görülür riskleri
> tespit etmek amaçlıdır.
> **Hiçbir gerçek secret değeri bu dosyada yer almıyor.** Bulgular yalnızca
> dosya:satır referansı ve pattern tanımı içerir; doğrulama, değerin var/yok
> (boş string mi değil mi) olduğunu test eden ikincil pattern'lerle yapıldı —
> değerin kendisi hiçbir aşamada okunmadı/yazılmadı.

## Yöntem

İki bağımsız geçiş yapıldı:
1. Read-only analiz ajanı (Explore) — kod okuma sırasında rastladığı hardcoded
   görünen değerleri, değerlerini yazmadan dosya:satır olarak işaretledi.
2. Bağımsız doğrulama — ben (ana oturum), aynı bulguları kendi grep pattern'lerimle
   (dosya içeriğini `files_with_matches` / sayım modunda, değeri göstermeden) tekrar
   test ettim. Aşağıdaki bulgular her iki geçişte de tutarlı çıktı.

## Bulgular

### P0 — Hardcoded credential-benzeri literal (kaynak kodda gömülü)

| Dosya | Satır (yakl.) | Pattern | Durum |
|---|---|---|---|
| `config/constants.py` | ~12 | `BOT_TOKEN = os.getenv('BOT_TOKEN', '<fallback>')` | Fallback boş string DEĞİL — doğrulandı. |
| `config/constants.py` | ~13 | `ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', '<fallback>'))` | `.env`'de bu isim hiç yok → her zaman fallback kullanılıyor. |
| `config/constants.py` | ~14 | `ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', "<fallback>")` | `.env`'de bu isim hiç yok → her zaman fallback kullanılıyor. |
| `config/constants.py` | ~21 | `DB_PASS = os.getenv("DB_PASS", "<fallback>")` | Fallback boş string DEĞİL — doğrulandı. |
| `config/constants.py` | ~29-30 | `EMAIL_USER` / `EMAIL_PASSWORD` fallback'leri | Gmail "uygulama şifresi" formatına benzer bir değer içeriyor gibi görünüyor. |
| `data/olimpos_data.py` | ~24 | İkinci, bağımsız bir `DB_PASS` fallback tanımı | Fallback boş string DEĞİL — doğrulandı. |
| `Olimpos_api_MEXC.py` | 159 | `API_KEY = "..."` doğrudan string literal | Boş string DEĞİL — doğrulandı (`API_KEY = ""` pattern'i eşleşmedi). |

**Neden P0:** Bu değerler kaynak kod dosyalarının içinde duruyor. Eğer bu proje
herhangi bir zamanda bir git deposuna (özellikle uzak/paylaşılan bir depoya) commit
edilmişse, bu credential'lar **sızmış sayılmalıdır** — geçmiş commit'lerde kalmış
olabilirler bile dosyalar sonradan temizlenmiş olsa.

**Önerilen aksiyon (kullanıcı tarafında, bu oturumun kapsamı dışında):**
1. Yukarıdaki tüm credential'ları (Telegram bot token, admin şifresi, DB şifresi,
   e-posta uygulama şifresi, MEXC API key + varsa eşleşen secret) **rotate/iptal et**.
2. `Gelisen_Bot` projesinin git geçmişini kontrol et — bu dosyalar commit edilmiş mi?
   Edilmişse geçmiş commit'lerde secret kalıcı olarak durur (BFG/git-filter-repo ile
   temizlik gerekebilir; bu, bu oturumun kapsamı dışında bir karardır).
3. `.env` ↔ kod isim uyuşmazlığını düzelt: kod `BOT_TOKEN` okuyor ama `.env`
   `TELEGRAM_BOT_TOKEN` tanımlıyor — bu isim çakışması nedeniyle sistem muhtemelen
   `.env`'i hiç kullanmadan hardcoded fallback ile çalışıyor olabilir; bu hem güvenlik
   hem operasyonel bir risktir (kullanıcı `.env`'i değiştirdiğini sanıp aslında hiçbir
   şey değiştirmemiş olabilir).

### P1 — Plaintext kullanıcı credential saklama

- `data/olimpos_data.py` (`get_api_key`) ve `data/credentials_repo.py` — kullanıcıların
  borsa `api_key`/`secret_key`/`passphrase` bilgileri PostgreSQL'de **düz metin**
  olarak saklanıyor. Şifreleme katmanına (Fernet/KMS/envelope encryption) rastlanmadı.
  DB'ye erişimi olan biri (yedek dosyası, sızıntı, insider vb.) tüm kullanıcıların
  borsa secret'larını doğrudan okuyabilir.

### P2 — İkili tanım / tutarsızlık riski

- DB bağlantı bilgileri iki ayrı yerde bağımsız tanımlanmış (`config/constants.py` ve
  `data/olimpos_data.py`) — aynı fallback mantığı iki kod yolunda tekrarlanmış; biri
  güncellenip diğeri unutulursa tutarsızlık/güvenlik açığı doğabilir.

## Kod deseni taraması (regex, dosya bazlı — .venv/bitget/okx/ta-lib hariç tutuldu)

Aşağıdaki pattern'ler proje genelinde (`*.py`) tarandı, yalnızca eşleşen **dosya
adları** raporlanıyor (içerik gösterilmedi):

| Pattern amacı | Eşleşen birinci-taraf dosya |
|---|---|
| Telegram bot token formatı (`\d{8,10}:[A-Za-z0-9_-]{30,40}`) | `config/constants.py` |
| `api_key`/`secret_key` değişkenine doğrudan string literal atama | `Olimpos_api_MEXC.py` |
| `PASSWORD`/`TOKEN`/`SECRET`/`API_KEY`/`PASSPHRASE` sabitine literal atama | `config/constants.py`, `Olimpos_api_MEXC.py` |
| PEM private key bloğu (`-----BEGIN ... PRIVATE KEY-----`) | Yok (yalnızca `.venv` içindeki kütüphane test dosyalarında, proje koduna ait değil) |
| `.env`/`.pem`/`credential`/`secret` isimli dosyalar | Yalnızca `.env` (beklenen) ve `data/credentials_repo.py` (isimden dolayı eşleşti — içerik taraması **temiz**, hardcoded değer yok, yalnızca DB'den okuma mantığı) |

## Snapshot alanına (`archive/gelisen_bot_snapshot/`) kopyalama öncesi ve sonrası kontrol

- Kopyalama **öncesi**: yukarıdaki P0 bulgusu olan 3 dosya (`config/constants.py`,
  `data/olimpos_data.py`, `Olimpos_api_MEXC.py`) kopyalama listesinden çıkarıldı;
  `.env` ve `olimpos_cripto_bot.zip` hiçbir zaman listeye alınmadı.
- Kopyalama **sonrası**: `archive/gelisen_bot_snapshot/` üzerinde aynı pattern taraması
  tekrar çalıştırıldı — **sıfır eşleşme**. Ayrıca `*.env*`, `*secret*`, `*credential*`
  isimli dosya taraması yapıldı — yalnızca `data/credentials_repo.py` (isimden dolayı,
  içerik temiz) eşleşti.

## Sonuç

Snapshot alanına kopyalanan 89 dosyada bilinen bir hardcoded secret pattern'i
bulunmamaktadır. Asıl risk, **kaynak projede** (Gelisen_Bot, kopyalanmadı) hâlâ duran
3 dosyadaki hardcoded credential'lardır — bunlar için üstteki "Önerilen aksiyon"
maddeleri kullanıcı tarafından değerlendirilmelidir. EzoEzgi tarafında finans
execution'ın gerçek anlamda açılabilmesi için ön koşul zaten ADR-010 ve BACKLOG B025
("API permission validator") ile karşılanıyor — yani bu bulgular EzoEzgi'nin mevcut
tasarımını doğruluyor, değiştirmiyor.
