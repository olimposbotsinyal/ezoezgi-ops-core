# Esik Degisikligi Uygulama (Apply) Raporu

Uretildi (UTC): 2026-08-13T18:04:12.925054+00:00
Proposal ID: HIGH_NULL_INTENT_RATE-20260813T180404
Alert: HIGH_NULL_INTENT_RATE
Mod: APPLY (degisiklikler uygulandi)
Sonuc: BASARILI

## Uygulanan degisiklikler

- Yamalanan esik turleri: warn, crit
- Eski checksum: `1677b2657abc8cb8e67e47c300b7aa8c7fcdc7410b013cd39eb2bd04c0bd719c`
- Yeni checksum: `800a37cd06ccf0a503d121dbb4616e8d81aa32e36b06d0edec7035206f9b0d73`
- Yedek: `d:\Projects\ezoezgi-ops\reports\threshold_apply_20260813T180412Z\backups\model_gateway_alerts.yaml.HIGH_NULL_INTENT_RATE-20260813T180404.backup`

## Yamalanamayan (non-patchable) alertler -- sistem geneli kayit

- `PRIMARY_RESTRICTED_PERSISTENT`: calibrate_alert_thresholds.ALERT_ENV_VAR_MAP'te warn/crit=None -- ayarlanabilir bir ortam degiskeni YOK (herhangi bir olusum > 0 tetikler, 'olmamasi gereken durum' tipi bir alert, oran-tabanli kalibrasyona uygun degil).
- `PREFLIGHT_UNKNOWN_PERSISTENT`: calibrate_alert_thresholds.ALERT_ENV_VAR_MAP'te warn/crit=None -- esik (>0.9) kodda sabit, ayarlanabilir bir ortam degiskeni YOK; degistirmek icin once model_gateway kod tarafinda bir env var eklenmesi gerekir.
- `CIRCUIT_OPEN_STUCK`: Sayisal/oransal bir esik degil -- boolean durum kontrolu (`model_gateway_circuit_open == 1`), esik kalibrasyonu/onay is akisinin kapsami disinda.

## VerifyReload dogrulamasi -- genel durum: **FAIL**

| Kontrol | Durum | Detay |
|---|---|---|
| promtool_check_rules | VERIFICATION_SKIPPED | PromtoolPath saglanmadi |
| amtool_check_config | FAIL | Checking 'd:\Projects\ezoezgi-ops\infra\monitoring\profiles\persistent\alertmanager.yml'  FAILED: invalid URL: unsupported scheme "" for URL

amtool.exe: error: failed to validate 1 file(s) |
| prometheus_health | VERIFICATION_SKIPPED | URL saglanmadi (opsiyonel parametre) |
| alertmanager_ready | VERIFICATION_SKIPPED | URL saglanmadi (opsiyonel parametre) |

