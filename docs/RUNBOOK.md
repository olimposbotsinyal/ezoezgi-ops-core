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

## Onay Gerektiren Aksiyonlar (Faz 4+ ile aktif olacak)

- Risk seviyesi `high` veya `irreversible` olan her aksiyon, kullanıcı onayı
  alınmadan yürütülmez.
- Onay/red kararı `data/audit` altına append-only olarak yazılır.
- Onay akışı henüz (Faz 0) yalnızca stub/simülasyon seviyesindedir — gerçek kullanıcı
  arayüzü Faz 4'te eklenecek.

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
