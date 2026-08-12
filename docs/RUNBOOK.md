# RUNBOOK — EzoEzgi Ops

> Operasyonel başvuru dokümanı. Servisler kod ile birlikte gelince buradaki komutlar
> güncellenecek; şu an Faz 0 (iskelet) için geçerli minimum akış.

## Günlük Çalışma Akışı

1. `docs/PLAN.md` içinde bugünün görevlerini kontrol et.
2. Görev tamamlanınca ilgili checkbox `[x]` yapılır.
3. Gün sonunda `docs/PLAN.md` → **Daily Log** bölümüne yeni giriş eklenir:
   - Yapılanlar
   - Sorunlar
   - Sonraki adım
4. Kapsam dışı yeni istekler `docs/BACKLOG.md`'ye eklenir, aktif sprint'e dahil edilmez.
5. Mimari/teknoloji kararları `docs/DECISIONS.md`'ye ADR olarak yazılır.

## Kimlik / Wake-Alias Değişikliği

- Kaynak dosya: `config/assistant.identity.json`.
- `wake_aliases` listesini düzenlemek restart gerektirmez (`alias_update_policy.requires_restart: false`).
- Değişiklikten sonra bir sonraki kullanıcı komutunda yeni alias'lar geçerli olmalı
  (bkz. PLAN.md T5 — config loader).

## E2E Demo Çalıştırma (T5–T14, T17 — finans DIŞI, Faz 0 çekirdek zincir)

Bu demo, finans içermeyen çekirdek zinciri (config → wake-alias → bridge →
risk kontrolü → onay kontrolü → cli-runner (echo) → audit log → TR yanıt)
uçtan uca gösterir. T14 ile resmen kapandı (bkz. PLAN.md T14).

**Kurulum (bir kez):**
```
cd D:\Projects\ezoezgi-ops
C:\Python313\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip pytest pyyaml
```

**Testleri çalıştırma:**
```
.\.venv\Scripts\python.exe -m pytest
```
Beklenen: `tests/` altındaki tüm testler (T5, T6, T7-T9, T10-T11, T12, T13,
T14, T17) yeşil — toplam 73 test. Test config'i (`pyproject.toml`)
`pythonpath` ile `apps/orchestrator/src`, `services/tr-en-bridge/src`,
`tools/cli-runner/src`, `scripts` dizinlerini otomatik ekler — testler
modülleri paket kurulumu olmadan doğrudan import edebilir.

**E2E demo'yu çalıştırma — Senaryo 1 (düşük risk, otomatik çalışır):**
```
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, echo ile 'merhaba' yaz"
```
Argümansız çalıştırılırsa varsayılan örnek cümle kullanılır. Beklenen çıktı:
```
[TR girdi] Ezo, echo ile 'merhaba' yaz
[TR yanit] Merhaba yazdırıldı.
[risk] low  [status] ok
[audit log] data\audit\audit.log.jsonl
```

**E2E demo'yu çalıştırma — Senaryo 2 (irreversible risk, onay bekliyor):**
```
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, tüm dosyaları sil"
```
Beklenen çıktı:
```
[TR girdi] Ezo, tüm dosyaları sil
[TR yanit] Bu işlem yüksek riskli (irreversible) olduğu için onay bekliyor. Devam etmek için onay vermeniz gerekiyor.
[risk] irreversible  [status] WAITING_APPROVAL
[audit log] data\audit\audit.log.jsonl
```
Bu senaryoda `run_echo`/`runner.run_command` hiç çağrılmaz — risk kontrolü
handler'dan önce devreye girer (bkz. "Risk Etiketleme ve Onay Akışı" altında).

Her iki senaryo da `data/audit/audit.log.jsonl`'a yeni bir JSONL satırı ekler
(append-only, bkz. ADR-009).

## Whitelist Yönetimi (CLI Runner, T12)

- Kaynak dosya: `config/cli_whitelist.json`.
- Yeni bir komut eklemek için `commands` altına yeni bir anahtar eklenir:
  ```json
  "yeni_komut": {
    "executable": "gercek-binary-adi",
    "description": "Ne yaptigi",
    "risk_level": "low",
    "timeout_seconds": 5
  }
  ```
- **Kurallar (asla bozulmamalı, bkz. ADR-012):**
  - `executable`, sistemde gerçekten bulunabilen (`shutil.which()` ile
    çözümlenebilen) bir program olmalı; aksi halde çağrı `EXECUTABLE_NOT_FOUND`
    ile başarısız olur (crash değil).
  - Komut asla bir shell string'i olarak değil, liste argümanlarıyla çalıştırılır
    (`shell=True` yasak) — whitelist'e `"executable": "bash -c ..."` gibi bir
    shell komutu **eklenmemeli**.
  - `timeout_seconds` verilmezse `default_timeout_seconds` (varsayılan 5)
    kullanılır; süresiz çalışan bir komut asla olmaz.
- Whitelist dışı bir `command_name` çağrılırsa (`runner.run_command`) hiçbir
  process başlamaz, `NOT_WHITELISTED` hata kodu döner.
- Yeni bir komut eklerken karşılığında `policies/risk/tool_risk_policy.yaml`'a
  da (T13) bir `risk_level` girişi eklenmesi önerilir — eklenmezse
  `default_risk_level` (`medium`, otomatik izinli ama en gevşek seviye değil)
  uygulanır.

## Risk Etiketleme ve Onay Akışı (T13)

- Kaynak dosya: `policies/risk/tool_risk_policy.yaml` — `tasks:` altında
  `task_en → risk_level` (`low`/`medium`/`high`/`irreversible`) eşlemesi.
- `apps/orchestrator/src/risk_engine.py::RiskEngine.get_risk(task_name)` bu
  dosyayı okur; policy'de tanımsız bir task, `default_risk_level`'a
  (varsayılan `medium`) düşer — **asla otomatik `low` sayılmaz**.
- `apps/orchestrator/src/approval_stub.py::check_approval(risk_level)`:
  - `low`/`medium` → `AUTO_ALLOWED` (orchestrator handler'ı direkt çağırır).
  - `high`/`irreversible` → `WAITING_APPROVAL` (handler'a **hiç gidilmez**,
    `Orchestrator.handle_task()` bu durumu döner).
- Bu aşamada gerçek bir approve/reject arayüzü yok (yalnızca stub) — Faz 4'te
  (bkz. MASTER_ROADMAP.md §8) gerçek onay akışı eklenecek. Şimdilik
  `WAITING_APPROVAL` durumu yalnızca audit log'a düşer ve TR yanıt olarak
  kullanıcıya bildirilir, otomatik yürütme olmaz.
- Yeni bir risk seviyesi eklemek/değiştirmek için `tool_risk_policy.yaml`'ı
  düzenlemek yeterli — kod değişikliği gerekmez.

**Bilinen ortam notları:**
- pytest'in varsayılan geçici dizini (`%TEMP%\pytest-of-<kullanıcı>`) bu ortamda
  `PermissionError` verebiliyor; `pyproject.toml`'da `addopts = "--basetemp=.pytest_tmp"`
  ile proje-lokal bir geçici dizine yönlendirildi (`.gitignore`'da).
- Windows konsolları varsayılan olarak UTF-8 olmayabilir; TR karakterler
  (`ı`, `ş`, `ğ` vb.) bu durumda ekranda bozuk görünebilir (veri bozulmaz,
  yalnızca render). `e2e_demo.py` bunu `sys.stdout.reconfigure(encoding="utf-8")`
  ile otomatik düzeltmeye çalışır; sorun devam ederse `chcp 65001` veya
  `$env:PYTHONIOENCODING="utf-8"` deneyin.

## Onay Gerektiren Aksiyonlar (Faz 4+ ile aktif olacak)

- Risk seviyesi `high` veya `irreversible` olan her aksiyon, kullanıcı onayı
  alınmadan yürütülmez.
- Onay/red kararı `data/audit` altına append-only olarak yazılır.
- Onay akışı henüz (Faz 0) yalnızca stub/simülasyon seviyesindedir — gerçek kullanıcı
  arayüzü Faz 4'te eklenecek.

## Finans Güvenlik Operasyonları

> Kapsam ve işlem seviyeleri (L0–L3) için bkz. `docs/MASTER_ROADMAP.md` §5;
> policy kaynağı `policies/risk/*.yaml` (bkz. PLAN.md T18).

### API Key Güvenliği

- Finance Engine'e tanımlanan her borsa API key'i **withdraw (çekme) yetkisi
  kapalı** olmak zorundadır; bağlantı öncesi izin doğrulaması yapılır (bkz.
  BACKLOG "API permission validator", B025).
- API key/secret **yalnızca `.env` veya vault** üzerinden sağlanır, hiçbir zaman
  `config/` altındaki JSON dosyalarına veya git'e yazılmaz (bkz. ADR-010).
- Key rotasyonu/iptali gerektiğinde ilgili borsa panelinden yapılır; sistem
  tarafında yalnızca yeni key `.env`'e güncellenir, eski işlemler audit log'da
  değişmeden kalır.

### Onay Seviyeleri

| Seviye | Onay | Operasyonel not |
|---|---|---|
| L0 — Bilgi | Yok | Salt okunur sorgular; risk yok |
| L1 — Simülasyon | Yok (loglanır) | Borsaya emir gitmez, mock sonuç üretilir |
| L2 — Küçük işlem | Tek onay | Onaylayan kullanıcı `data/audit`'e kaydedilir |
| L3 — Büyük işlem | Çift onay + bekleme süresi | Bekleme süresi dolmadan yürütme yok; iki farklı onay adımı zorunlu |

- Onay/red kararları CLI simülasyonu üzerinden verilir (bkz. PLAN.md T20);
  gerçek kullanıcı arayüzü ileri fazda eklenecek.
- Onay bekleyen bir işlem, süre dolmadan veya reddedilmeden **kendiliğinden
  yürütülmez**.

### Acil Durdurma (Kill Switch)

- Şüpheli/istenmeyen işlem tespit edildiğinde ilk adım: Finance Engine'in
  yürütme yetkisini durdurmak (ör. config/flag üzerinden `execution_enabled:
  false` — bu görev henüz kod olarak yazılmadı, bkz. BACKLOG "Circuit breaker").
- Kill switch aktifken: L0/L1 (bilgi/simülasyon) çalışmaya devam edebilir,
  L2/L3 (gerçek işlem) tamamen bloklanır.
- Kill switch aktivasyonu/deaktivasyonu da audit log'a yazılır (kim, ne zaman,
  neden).

### Hatalı İşlemde Geri Alma Prosedürü

- **Önemli sınır:** Borsaya gerçekten iletilmiş bir emir (fiyat/miktar hatası
  dahil) genel olarak **geri alınamaz** — yalnızca ters işlemle (karşı pozisyon
  açma) etkisi azaltılabilir; bu bir "geri alma" değil, yeni bir işlemdir ve
  aynı onay seviyesine tabidir.
- Geri alma/telafi mümkün olan senaryolar:
  - İşlem henüz **onay bekliyor** (L2/L3, süre dolmadı) → reject ile iptal
    edilir, borsaya hiç gitmez.
  - İşlem **L1 (simülasyon)** seviyesinde → zaten gerçek değildir, iptal =
    mock kaydın "cancelled" olarak işaretlenmesi.
- Her durumda ilk adım: kill switch ile yeni işlemleri durdur, ardından
  mevcut pozisyonu manuel olarak (kullanıcı onayıyla) değerlendir.
- Post-mortem: Olay `data/audit` kayıtlarına referansla `docs/DECISIONS.md`'ye
  gerekirse bir ADR olarak (politika değişikliği varsa) işlenir.

## Sorun Giderme (Genel)

| Belirti | Olası Neden | İlk Bakılacak Yer |
|---|---|---|
| Wake-alias tanınmıyor | Config yüklenmemiş / eski | `config/assistant.identity.json`, config loader logu |
| TR yanıt anlamsız/bozuk | Bridge çeviri hatası | `services/tr-en-bridge` logu, ADR-001 (Ollama) bağlantısı |
| Tool-call hiç çalışmıyor | Whitelist dışı komut | `tools/cli-runner` whitelist tanımı |
| Aksiyon sessizce yürütülmüyor | Onay bekliyor (beklenen davranış) | `data/audit` log kaydı |

## İlgili Dokümanlar

- Vizyon/mimari: `docs/MASTER_ROADMAP.md`
- Aktif sprint: `docs/PLAN.md`
- Ertelenen/gelecek işler: `docs/BACKLOG.md`
- Mimari kararlar: `docs/DECISIONS.md`
