# B036 — Upstream Watch Protokolü

> **Durum: WATCH MODE AKTİF.** Maintainer yanıtı (diagnostic knob veya
> patch/sürüm önerisi) gelene kadar **yeni geniş kapsamlı deney partisi
> çalıştırılmayacak** — bkz. `validation_on_trigger.md`. Bu protokol,
> upstream issue'yu düzenli aralıklarla kontrol etme ve yalnızca somut bir
> tetikleyici (trigger) geldiğinde harekete geçme kuralını tanımlar.

## Kapsam

B036 — Ollama Windows'ta `0xc0000005` (`llama-server` access violation)
runtime kararsızlığı, Vulkan/GPU backend etkileşimiyle log-kanıtlı ilişkili
(bkz. `docs/BACKLOG.md` B036 satırı,
`reports/runtime_incident_20260813T004855Z/gpu_isolation_matrix.md`,
`reports/runtime_incident_20260813T004855Z/exp_01_post_upstream/summary.md`).

## Kaynak (source of truth)

- Issue URL: <https://github.com/ollama/ollama/issues/17716>
- Manuel takip yorumu (permalink): <https://github.com/ollama/ollama/issues/17716#issuecomment-5275320830>
  — **not:** bu permalink kullanıcı tarafından bildirildi; taban issue
  sayfasının varlığı/içeriği WebFetch ile doğrulandı, ancak GitHub yorum
  fragment'leri (`#issuecomment-...`) HTTP isteğine dahil edilmediğinden
  (tarayıcı-taraflı anchor), bu ortamdaki araçlarla **spesifik yorumun
  kendisi tek başına, fragment üzerinden bağımsız olarak doğrulanamadı**.
- **Kanonik doğrulama referansı (audit için sunucu-adreslenebilir):**
  - Comment ID: `5275320830`
  - Comment API URL: <https://api.github.com/repos/ollama/ollama/issues/comments/5275320830>
  - Bu URL, fragment'ten farklı olarak gerçek bir HTTP kaynağı olduğundan
    WebFetch ile **doğrudan doğrulandı**: yorum gerçekten mevcut, yazar
    `olimposbotsinyal`, issue #17716 üzerinde, içeriği bu projenin
    hazırladığı takip taslağıyla (`upstream_comment_draft_17716.md`)
    birebir eşleşiyor (n=100, senaryo A-E, 80/100 çöküş istatistikleri
    dahil). Bu, permalink'in üstündeki fragment-sınırlaması notunu
    **geçersiz kılmıyor** (fragment hâlâ tek başına doğrulanamaz) ama
    aynı yorumu farklı, sunucu-taraflı bir kanaldan kanıtlıyor.

## Kontrol sıklığı (poll cadence)

Günde **2 kez**, Europe/Istanbul saatiyle:

- 09:30
- 17:30

Bu, otomatik bir zamanlanmış görev değildir — kullanıcı veya bir sonraki
oturum, bu saatlere yakın bir zamanda issue'yu manuel kontrol eder ve
`upstream_watch_log.md`'ye bir satır ekler.

## Her kontrolde toplanacaklar

Her kontrol turunda şu sorulara yanıt aranır ve `upstream_watch_log.md`'ye
kaydedilir:

1. Yeni bir maintainer yorumu var mı? (E/H)
2. Ek tanı verisi (log, flag, debug build) istendi mi?
3. Bir patch/commit referansı paylaşıldı mı?
4. Yeni bir Ollama sürümü/ipucu belirtildi mi?

## Karar ağacı

- **Maintainer güncellemesi yok** → `upstream_watch_log.md`'ye
  `NO_ACTION` olarak kaydet, hiçbir deney çalıştırma.
- **Maintainer ek veri istiyor** → yalnızca istenen hedefli veriyi topla
  (geniş kapsamlı yeni bir deney partisi çalıştırma) — bkz.
  `validation_on_trigger.md` Trigger A.
- **Maintainer patch/commit/sürüm önerdi** → doğrulama koşusu çalıştır —
  bkz. `validation_on_trigger.md` Trigger B/C.

## İlgili dokümanlar

- `reports/runtime_incident_20260813T004855Z/upstream_watch_log.md` —
  kontrol geçmişi tablosu
- `reports/runtime_incident_20260813T004855Z/validation_on_trigger.md` —
  tetikleyici bazlı doğrulama planı (Trigger A/B/C)
- `docs/BACKLOG.md` B036 satırı — genel durum
- `docs/RUNBOOK.md` "B036 Upstream Watch Mode Enabled" — kısa özet
