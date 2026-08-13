# 50 Çağrılık Stabilite Kapısı — SONUÇ: ÇALIŞTIRILMADI

**Neden:** Görev talimatı gereği ("herhangi bir tekli test başarılı olursa
batch stability testi çalıştır") — Bölüm 3'teki 4 tekli-çağrı deneyinden
(A, B, C, D) **hiçbiri başarılı olmadı**; hepsi aynı `0xc0000005`
(`llama-server` access violation) imzasıyla `HTTP 500` döndürdü (bkz.
`stability_steps.md`).

Bu durumda 50 çağrılık bir batch testi çalıştırmak, aynı hatayı 50 kez
tekrar üretip zaman/kaynak harcamaktan başka bir şey olmazdı — sonucun
`success_count=0`, `http_500_count=50`, `crash_count=50` olacağı, tek
çağrı sonuçlarından zaten kesin şekilde biliniyor. Bu, hard gate
kriterlerinin (`success_count=50`, `http_500_count=0`, `crash_count=0`)
**FAIL** olarak değerlendirilmesi için yeterli kanıt.

## Gate sonucu

- **success_count:** 0 (denenmedi, ama tekli testlerden 0/4 başarı oranı
  biliniyor)
- **http_500_count:** N/A (batch koşulmadı) — tekli testlerde 4/4 = HTTP 500
- **crash_count:** N/A (batch koşulmadı) — tekli testlerde 4/4 = `0xc0000005`
- **p50/p95 latency:** N/A (batch koşulmadı)
- **Gate kararı:** **FAIL** (koşulmadan, tekli test kanıtına dayanarak)

`batch_50_raw.jsonl` bilerek boş bırakıldı — sahte/simüle edilmiş veri
yazılmadı.

## Sonraki adım

B036'nın kabul kriterini (canlı Ollama gate'i yeniden koşmadan önce
50/50 başarılı, çökmeyen inference çağrısı) karşılamak için önce
`llama-server.exe`'nin bu makinede neden her zaman çöktüğü çözülmeli —
bkz. `stability_steps.md` "Ara değerlendirme" ve
`docs/BACKLOG.md` B036.
