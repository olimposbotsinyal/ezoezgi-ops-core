# Upstream Issue — Gönderime Hazır Paket (AÇILDI)

**Durum: AÇILDI.** Issue başarıyla upstream'e açıldı; takip maintainer
yanıtı ve patch doğrulaması üzerinden sürdürülecek.

- Issue URL: <https://github.com/ollama/ollama/issues/17716>
- Gönderim zamanı (Europe/Istanbul): 2026-08-13 ~05:02 (+03:00)
- Gönderen: kullanıcı, aşağıdaki paketi manuel kopyala-yapıştır ile kullanarak

> Aşağıdaki bölümler, gönderim öncesi hazırlanan orijinal paketin
> (başlık, gövde, ek dosya listesi, adım adım talimat) değişmemiş kaydıdır —
> teknik kanıt/talimat içeriği düzenlenmedi.

## Neden otomatik açılamadı

- `gh` CLI bu makinede kurulu değil (`where gh` → bulunamadı).
- `GITHUB_TOKEN` / `GH_TOKEN` ortam değişkeni tanımlı değil.
- `ollama/ollama`, bu projenin sahip olmadığı üçüncü taraf bir açık kaynak
  depo — kimlik doğrulama olmadan issue açılamaz, ve var olmayan bir
  kimlik bilgisi/URL uydurulmadı.

## Gönderim adımları (manuel)

1. https://github.com/ollama/ollama/issues/new adresine git.
2. Aşağıdaki **Başlık**'ı kopyala-yapıştır.
3. Aşağıdaki **Gövde**'yi kopyala-yapıştır (kaynak: `OLLAMA_GITHUB_ISSUE_DRAFT.md`,
   İngilizce — upstream deponun dili).
4. Aşağıda listelenen dosyaları issue'ya ek (attachment) olarak sürükle-bırak
   ile ekle (GitHub issue formu dosya sürüklemeyi destekler).
5. Gönderdikten sonra oluşan issue URL'sini `docs/RUNBOOK.md`'deki
   "B036 Upstream Issue" bölümüne ekle (bu triage paketinin bir sonraki
   güncellemesinde).

## Başlık (kopyala-yapıştır)

```
llama-server crashes with 0xc0000005 on Windows when using Vulkan backend on older NVIDIA driver (Quadro RTX 3000, driver 442.94)
```

## Gövde (kopyala-yapıştır)

> Tam metin için bkz. `OLLAMA_GITHUB_ISSUE_DRAFT.md` — bu dosyanın
> "Environment" başlığından itibaren tüm içeriği (üstteki taslak notu hariç)
> doğrudan issue gövdesi olarak kullanılabilir.

## Ek olarak yüklenecek dosyalar (bu klasörden)

- `host_fingerprint.md`
- `event_viewer_crash_entries.txt`
- `gpu_isolation_matrix.md`
- `version_ab_test.md`
- `matrix_A_stderr.log` (Vulkan etkin, çöküş logu)
- `matrix_B_stderr.log` (`OLLAMA_VULKAN=false`, başarılı — karşılaştırma için)
- `repro_output.txt`
- `../../scripts/repro_ollama_crash.ps1` (repo kökünden: `scripts/repro_ollama_crash.ps1`)

## Gönderim sonrası

Issue açıldıktan sonra:
1. URL'yi `docs/RUNBOOK.md` → "B036 Upstream Issue" bölümüne ekle
   (açılış zaman damgası + kısa özet + beklenen takip: maintainer yanıtı /
   patch takibi).
2. `docs/BACKLOG.md` B036 satırına issue URL'sini referans olarak ekle.
3. Bu paketin durumunu `READY_TO_SUBMIT` → `AÇILDI` olarak güncelle.

**Güncel durum: AÇILDI — <https://github.com/ollama/ollama/issues/17716>.**
Issue başarıyla upstream'e açıldı; takip maintainer yanıtı ve patch
doğrulaması üzerinden sürdürülecek.
