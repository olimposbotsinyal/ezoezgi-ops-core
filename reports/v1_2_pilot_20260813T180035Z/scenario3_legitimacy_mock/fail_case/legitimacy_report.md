# Acil Durum Mesruiyet On-Kontrolu (PILOT, non-blocking)

Uretildi (UTC): 2026-08-13T18:04:59.851176+00:00
incident_id: `TICKET-BAD-FORMAT`
provider: `mock`
Sonuc: **FAIL**

## Nedenler

- 'TICKET-BAD-FORMAT' desene uymuyor (beklenen: ^OPS-\d+$)

**NOT (PILOT):** Bu kontrol HENUZ hicbir apply akisini ENGELLEMEZ -- yalnizca bilgilendiricidir. `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1` olsa bile v1.2'de zorlayici DEGILDIR (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "Promotion criteria from pilot to enforced").

