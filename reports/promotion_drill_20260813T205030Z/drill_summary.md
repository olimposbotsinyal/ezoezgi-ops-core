# Kontrollu VerifyReload FAIL + Auto-Rollback Drill Ozeti

Paketlendi (UTC): 2026-08-13T20:50:30.243649+00:00

**SEFFAFLIK NOTU (ONEMLI):** Bu paket, BU OTURUMDA calistirilmis TAZE bir drill DEGILDIR -- bu ortamda `promtool`/`amtool` kurulu olmadigi icin GERCEK bir yeni calistirma yapilamadi. Bunun yerine, DAHA ONCE (kaynak: `reports\v1_2_pilot_20260813T180035Z\scenario2_verify_fail_autorollback\autorollback_ON_strict\apply_report.json`, uretildi: 2026-08-13T18:04:43.999833+00:00) GERCEK `amtool.exe` ile GERCEKTEN calistirilmis, tum kabul kriterlerini karsilayan bir drill'in kanitina SEFFAF SEKILDE REFERANS verir/paketler. Fabrike edilmis hicbir veri YOKTUR -- kaynak dosya BIREBIR okunup dogrulanmistir.

Kaynak apply_report.json: `reports\v1_2_pilot_20260813T180035Z\scenario2_verify_fail_autorollback\autorollback_ON_strict\apply_report.json`
proposal_id: `HIGH_NULL_INTENT_RATE-20260813T180435`

## Kabul kriterleri

- verification_state: `FAIL` (beklenen: `FAIL`)
- auto_rollback.triggered: `True`
- auto_rollback.restored: `True`
- old_checksum: `1677b2657abc8cb8e67e47c300b7aa8c7fcdc7410b013cd39eb2bd04c0bd719c`
- auto_rollback.restored_checksum: `1677b2657abc8cb8e67e47c300b7aa8c7fcdc7410b013cd39eb2bd04c0bd719c`
- checksum eslesmesi: EVET

**Sonuc: KABUL EDILDI -- tum kriterler saglandi**

## Audit log kaniti

`data/audit/audit.log.jsonl`'de eslesen kayit BULUNDU: `task=auto_rollback_triggered`, `timestamp=2026-08-13T18:04:44.031202+00:00`, `status=ROLLED_BACK`.
