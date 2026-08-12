# Faz 0 Kapanış Özeti — EzoEzgi Ops

> Durum: **Kapandı** (çekirdek kapsam) — 2026-08-13
> Branch: `feature/core-bootstrap-week1`
> İlgili: [docs/PLAN.md](../PLAN.md), [docs/MASTER_ROADMAP.md](../MASTER_ROADMAP.md),
> [docs/DECISIONS.md](../DECISIONS.md), [docs/RUNBOOK.md](../RUNBOOK.md),
> [docs/BACKLOG.md](../BACKLOG.md)

## 1. Tamamlanan Görevler

| Görev | Özet | Kanıt |
|---|---|---|
| T1 | Monorepo klasör iskeleti | `docs/ config/ apps/ services/ tools/ infra/ data/ policies/` |
| T2 | Git deposu | İlk commit (`3f498ca`) |
| T3 | Kök README + docs indeksi + çalıştırma adımı | `README.md` |
| T4 | `assistant.identity.json` (kimlik/alias config) | `config/assistant.identity.json` |
| T5 | Config loader (hot-reload) | `apps/orchestrator/src/config_loader.py`, 7 test |
| T6 | Wake-alias eşleme (`ezo`/`ezgi`) | `services/tr-en-bridge/src/alias_matcher.py`, 23 test |
| T7 | TR-EN bridge iskeleti (mock) | `services/tr-en-bridge/src/bridge.py` |
| T8 | Ollama bağlantı noktası (graceful fail) | `services/tr-en-bridge/src/model_client.py`, 6 test |
| T9 | Bridge round-trip testi | `tests/test_bridge_roundtrip.py`, 4 test |
| T10 | Orchestrator iskeleti | `apps/orchestrator/src/orchestrator.py` |
| T11 | Ajan/tool registry | `apps/orchestrator/src/registry.py`, 7 test (T10+T11 birlikte) |
| T12 | Whitelist tabanlı CLI runner | `tools/cli-runner/src/runner.py` + `config/cli_whitelist.json`, 7 test |
| T13 | Risk etiketleme + onay stub'ı | `risk_engine.py` + `approval_stub.py` + `policies/risk/tool_risk_policy.yaml`, 12 test |
| T14 | Resmi E2E kabul (TR→EN→tool→TR + audit) | `scripts/e2e_demo.py`, 3 test |
| T17 | Audit logger (JSONL, append-only, genel amaçlı) | `apps/orchestrator/src/audit_logger.py`, 4 test |

Ayrıca kapsam dışı bir ek çalışma olarak: **Gelisen_Bot analizi** (eski bir
kripto trading botunun salt-analizi, aktif entegrasyon yok) tamamlandı —
bkz. `docs/imports/GELISEN_BOT_*` ve `archive/gelisen_bot_snapshot/`.

## 2. Test Özeti

**73/73 test yeşil** (`./.venv/Scripts/python.exe -m pytest`, ~4 saniye).

| Dosya | Test sayısı |
|---|---|
| `test_alias_matcher.py` | 23 |
| `test_audit_logger.py` | 4 |
| `test_bridge_roundtrip.py` | 4 |
| `test_cli_runner.py` | 7 |
| `test_config_loader.py` | 7 |
| `test_e2e_acceptance.py` | 3 |
| `test_model_client.py` | 6 |
| `test_orchestrator.py` | 7 |
| `test_risk_approval_flow.py` | 12 |
| **Toplam** | **73** |

E2E kabul kanıtı (`python scripts/e2e_demo.py`):
- `"Ezo, echo ile 'merhaba' yaz"` → `"Merhaba yazdırıldı."`, `risk=low`, `status=ok`
- `"Ezo, tüm dosyaları sil"` → onay bekliyor mesajı, `risk=irreversible`, `status=WAITING_APPROVAL`

## 3. Güvenlik Kazanımları

- **Whitelist tabanlı komut çalıştırma (T12, ADR-012):** `tools/cli-runner`
  yalnızca `config/cli_whitelist.json`'da tanımlı komutları çalıştırır;
  `shell=True` hiçbir yerde kullanılmaz (liste argümanlı `subprocess.run`),
  her çağrıda zorunlu timeout uygulanır, whitelist dışı istek hiçbir process
  başlatmadan deterministic bir hata koduyla reddedilir.
- **Risk etiketleme + onay akışı (T13):** Her tool-call, handler'a ulaşmadan
  önce `policies/risk/tool_risk_policy.yaml` üzerinden bir risk seviyesine
  (`low`/`medium`/`high`/`irreversible`) tabi tutulur; `high`/`irreversible`
  seviyesindeki hiçbir aksiyon otomatik yürütülmez, `WAITING_APPROVAL`
  durumuna düşer. Tanımsız bir task asla "low" sayılmaz (güvenli varsayılan:
  `medium`).
- **Append-only audit log (T17, ADR-009):** Alias, task, risk seviyesi ve
  sonuç dahil her karar `data/audit/audit.log.jsonl`'a satır bazlı,
  değiştirilemez şekilde yazılır — hem düşük riskli otomatik-izinli hem de
  onay bekleyen aksiyonlar için.
- **Secret hijyeni (Gelisen_Bot analizinden):** Kaynak kodda hardcoded
  credential tespiti yapıldı, hiçbir secret EzoEzgi'ye kopyalanmadı; bulgular
  `docs/imports/GELISEN_BOT_SECURITY_NOTES.md`'de. Bu deneyim, EzoEzgi'nin
  kendi config/secret prensibine (ADR-010: yalnızca `.env`/vault, hardcoded
  fallback yasak) doğrudan girdi sağladı.

## 4. Bilinçli Ertelenenler

| Görev | Neden ertelendi | Ne zaman gündeme gelir |
|---|---|---|
| T18 — Finans-özel risk policy (`execution_levels.yaml`, `finance.yaml`, L0-L3) | Genel `tool_risk_policy.yaml` (T13) ile karıştırılmasın diye ayrı tutuldu; finans kapsamı Gelisen_Bot parity analizine bağlı | Faz 2, parity checklist tamamlandıktan sonra |
| T19 — Finance execution mock | **Paused (Phase-2+)** — Gelisen_Bot'ta tespit edilen mimari/güvenlik sorunlarının (plaintext credential, tutarsız execution kodu) EzoEzgi'ye taşınmaması için bilinçli durduruldu | Faz 2, bkz. ADR-011 ve `docs/imports/GELISEN_BOT_PARITY_CHECKLIST.md` |
| T20 — Onay akışı CLI simülasyonu (approve/reject) | T19'a bağımlı | Faz 2, T19 sonrası |

Finans execution'ın gerçek anlamda açılması için önce: secret temizliği,
`ExchangeConnector` soyutlaması (B021), credential encryption (B030) ve
API permission validator (B025) tamamlanmalı — bkz. `docs/BACKLOG.md`.

## 5. Faz 1'e Giriş Kriterleri

MASTER_ROADMAP.md §7'ye göre Faz 1 ("Çekirdek Döngü") odak alanı: E2E'nin
gerçek (mock olmayan) model bağlantısıyla çalışması ve CLI runner'ın
genişletilmesi. Faz 1'e geçmeden önce doğrulanması gerekenler:

- [x] Faz 0 çekirdek zinciri uçtan uca çalışıyor (T5-T14, T17) — bu doküman.
- [x] Güvenlik temelleri (whitelist, risk/onay, audit) yerinde — §3.
- [ ] T15 — Faz 0 gözden geçirme + BACKLOG güncelleme (bu kapanış dokümanı
      kısmen karşılıyor; PLAN.md'de resmi checkbox henüz işaretlenmedi).
- [ ] T16 — Faz 1 kapsamının taslağını PLAN.md'ye eklemek (henüz yapılmadı).
- [ ] B031 — Gerçek TR-EN/NLU entegrasyonu (mock keyword sınıflandırmanın
      yerini alacak) — Faz 1'in asıl teknik hedefi.
- [ ] B034 karşılığı genişleme — cli-runner whitelist'ine yeni, gerçek
      araçlar (dosya okuma, tarayıcı vb.) eklenmesi.
- [ ] Python bağımlılık dosyası (B032) — `pytest`+`pyyaml` şu an yalnızca
      ad-hoc `.venv`'e kurulu, reproducibility için resmi bir dependency
      dosyası gerekiyor.

**Sonuç:** Faz 0'ın çekirdek (finans dışı) kapsamı kapalı ve test edilmiş
durumda. Faz 1'e resmi geçiş için T15/T16'nın PLAN.md'de tamamlanması ve
B031/B032'nin en azından planlanması önerilir.
