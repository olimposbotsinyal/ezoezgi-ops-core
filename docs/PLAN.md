# PLAN — İlk 14 Gün (Faz 0: Bootstrap)

> Kapsam: Monorepo iskeleti, kimlik/alias config, TR-EN köprü iskeleti, orchestrator
> iskeleti, CLI runner iskeleti, basit E2E (TR komut → EN task → tool call → TR yanıt).
> Kural: Sprint ortasında kapsam değişikliği yok — yeni istekler BACKLOG.md'ye yazılır.

## Gün 1–2 — Monorepo ve Klasör İskeleti

- [x] **T1. Klasör yapısını oluştur**
  - Amaç: Tüm ekiplerin/ajanların ortak dizin sözleşmesine sahip olması.
  - Teknik çıktı: `docs/ config/ apps/ services/ tools/ infra/ data/ policies/` ağacı.
  - Kabul kriteri: Belirlenen tüm alt klasörler mevcut, boş klasörlerde `.gitkeep`.
  - Bağımlılıklar: Yok.

- [x] **T2. Git deposu başlat**
  - Amaç: Sürüm takibini gün 1'den itibaren garanti altına almak.
  - Teknik çıktı: `git init`, `.gitignore`, ilk commit.
  - Kabul kriteri: `git log` en az 1 commit gösteriyor, `git status` temiz.
  - Bağımlılıklar: T1.

- [ ] **T3. Kök README ve lisans/politika notu**
  - Amaç: Depoya yeni katılan biri veya ajanın 1 dakikada bağlam kazanması.
  - Teknik çıktı: `README.md` (proje özeti + docs/ indeksine link).
  - Kabul kriteri: README, MASTER_ROADMAP.md ve PLAN.md'ye link veriyor.
  - Bağımlılıklar: T1.

## Gün 3–4 — Kimlik / Alias Config Altyapısı

- [x] **T4. `assistant.identity.json` oluştur**
  - Amaç: Asistan adı ve wake-alias listesinin tek doğruluk kaynağı olması.
  - Teknik çıktı: `config/assistant.identity.json` (assistant_id, display_name,
    wake_aliases, language_mode, alias_update_policy).
  - Kabul kriteri: JSON şema doğrulanabilir; `wake_aliases` en az `["ezo","ezgi"]` içerir.
  - Bağımlılıklar: T1.

- [ ] **T5. Config loader (basit)**
  - Amaç: Servislerin identity config'i restart gerektirmeden okuyabilmesi.
  - Teknik çıktı: `apps/orchestrator` içinde config okuma modülü (dosya değişimini
    poll veya watch eden basit fonksiyon).
  - Kabul kriteri: Config dosyası değiştirildiğinde bir sonraki komutta yeni alias
    listesi kullanılıyor (restart yok).
  - Bağımlılıklar: T4.

- [ ] **T6. Wake-alias eşleme testi**
  - Amaç: "Ezo" / "Ezgi" ifadelerinin doğru tanınmasını garanti etmek.
  - Teknik çıktı: Basit unit test seti (ör. 10 örnek cümle, alias'lı/alias'sız).
  - Kabul kriteri: Tüm test örnekleri doğru sınıflandırılıyor (pass).
  - Bağımlılıklar: T5.

## Gün 5–7 — TR-EN Köprü Servis İskeleti

- [ ] **T7. `tr-en-bridge` servis iskeleti**
  - Amaç: TR girdi → EN task çıktısı üreten servisin minimum çalışan formu.
  - Teknik çıktı: `services/tr-en-bridge` altında giriş/çıkış arayüzü tanımlı (ör.
    `translate_and_extract(input_tr) -> {task_en, original_tr}`), henüz gerçek
    model bağlanmamış (stub/mock çeviri).
  - Kabul kriteri: Stub servis, örnek TR cümleyi alıp sabit/mock EN task döndürüyor.
  - Bağımlılıklar: T1.

- [ ] **T8. Local model bağlantı noktası (Ollama) tanımı**
  - Amaç: Gerçek çeviri/anlama için local runtime bağlantısının yerinin netleşmesi.
  - Teknik çıktı: `services/tr-en-bridge` içinde Ollama çağrısı için ayrılmış
    interface/config (henüz zorunlu canlı bağlantı değil).
  - Kabul kriteri: Ollama servis adresi config'ten okunuyor; servis yoksa graceful
    hata/log veriyor (crash yok).
  - Bağımlılıklar: T7.

- [ ] **T9. Bridge round-trip testi (mock)**
  - Amaç: TR→EN→TR döngüsünün uçtan uca çalıştığını mock veriyle kanıtlamak.
  - Teknik çıktı: Basit test: `"Ezo, bugünkü harcamaları göster"` → mock EN task →
    mock TR yanıt.
  - Kabul kriteri: Test yeşil; wake-alias doğru yakalanıyor (T6 ile birlikte).
  - Bağımlılıklar: T7, T6.

## Gün 8–9 — Orchestrator Skeleton

- [ ] **T10. `orchestrator` servis iskeleti**
  - Amaç: Task graph'ı alıp uygun ajana/tool'a yönlendirecek çekirdeğin taslağı.
  - Teknik çıktı: `apps/orchestrator` altında `handle_task(task_en) -> result_en`
    fonksiyonu; ilk sürümde tek bir sabit tool-call rotası (ör. CLI runner'a echo).
  - Kabul kriteri: Orchestrator, bridge'den gelen mock task'ı alıp tool-call
    tetikleyebiliyor.
  - Bağımlılıklar: T7.

- [ ] **T11. Ajan kayıt (registry) taslağı**
  - Amaç: İleride yeni ajan eklemenin (finance, research, vb.) tek noktadan
    yönetilmesi.
  - Teknik çıktı: Basit registry yapısı (ajan adı → handler referansı), şimdilik
    yalnızca `cli-runner` kayıtlı.
  - Kabul kriteri: Registry'e yeni bir mock ajan eklenip orchestrator üzerinden
    çağrılabiliyor.
  - Bağımlılıklar: T10.

## Gün 10–11 — CLI Runner Skeleton

- [ ] **T12. `cli-runner` tool iskeleti**
  - Amaç: Orchestrator'ın gerçek bir sistem komutunu (sandboxed) çalıştırabilmesi.
  - Teknik çıktı: `tools/cli-runner` altında whitelist'li, güvenli komut çalıştırma
    fonksiyonu (ör. yalnızca izinli komut listesi çalıştırılabilir).
  - Kabul kriteri: Whitelist dışı komut reddediliyor; izinli komut (ör. `echo`)
    başarıyla çalışıp sonucu döndürüyor.
  - Bağımlılıklar: T1.

- [ ] **T13. Risk etiketleme + onay stub'ı**
  - Amaç: §8 (Güvenlik ve Onay Mekanizması) için ilk iskeleti erken kurmak.
  - Teknik çıktı: Her tool-call'a `risk_level` alanı eklenmesi; `high`/`irreversible`
    için "onay bekliyor" durumunu simüle eden stub.
  - Kabul kriteri: `low` risk komut direkt çalışıyor; `high` risk komut onay
    olmadan bloklanıyor (log'a düşüyor).
  - Bağımlılıklar: T12.

## Gün 12–14 — Basit E2E ve Faz Kapanışı

- [ ] **T14. E2E entegrasyon: TR komut → EN task → tool call → TR yanıt**
  - Amaç: Tüm iskeletin uçtan uca, gerçek (mock olmayan akış, mock model) bir
    örnek üzerinden çalıştığını göstermek.
  - Teknik çıktı: Tek komutla çalıştırılabilir demo script/senaryo.
  - Kabul kriteri: `"Ezo, echo ile 'merhaba' yaz"` girdisi → orchestrator → cli-runner
    (echo) → bridge → `"Merhaba yazdırıldı."` TR yanıtı üretiyor; audit log'a kayıt
    düşüyor.
  - Bağımlılıklar: T6, T9, T11, T13.

- [ ] **T15. Faz 0 gözden geçirme + BACKLOG güncelleme**
  - Amaç: Kapsam dışı kalan / ertelenen konuların BACKLOG'a taşınması.
  - Teknik çıktı: BACKLOG.md güncellemesi, DECISIONS.md'de varsa yeni ADR'ler.
  - Kabul kriteri: Açık PLAN.md maddesi kalmıyor (ya tamam ya BACKLOG'a taşınmış).
  - Bağımlılıklar: T14.

- [ ] **T16. Faz 1 kapsamının taslağını çıkar**
  - Amaç: Faz 0'dan Faz 1'e (Çekirdek Döngü) sorunsuz geçiş.
  - Teknik çıktı: MASTER_ROADMAP.md §7 ile uyumlu, PLAN.md'ye Faz 1 bölümü eklenmesi
    (bu bootstrap kapsamında yalnızca taslak başlıkları).
  - Kabul kriteri: Faz 1 için en az 5 üst seviye görev BACKLOG.md'de mevcut.
  - Bağımlılıklar: T15.

---

## Daily Log

### 2026-08-12
- **Yapılanlar:** Proje bootstrap edildi — klasör iskeleti, `assistant.identity.json`,
  MASTER_ROADMAP.md, PLAN.md, BACKLOG.md, DECISIONS.md oluşturuldu; git deposu başlatıldı.
- **Sorunlar:** Yok (henüz kod yazılmadı, yalnızca iskelet).
- **Sonraki adım:** T3 (README) ve T5 (config loader) ile devam.
