# Acil Durum Mesruiyet On-Kontrolu (PILOT, non-blocking)

Uretildi (UTC): 2026-08-13T18:04:59.659456+00:00
incident_id: `OPS-5001`
provider: `mock`
Sonuc: **PASS**

## Nedenler

- ticket format gecerli
- [PILOT STUB -- gercek baglanti/sir YOK] provider=mock, ticket=OPS-5001 'acik' varsayildi

**NOT (PILOT):** Bu kontrol HENUZ hicbir apply akisini ENGELLEMEZ -- yalnizca bilgilendiricidir. `GOV_EMERGENCY_LEGITIMACY_REQUIRED=1` olsa bile v1.2'de zorlayici DEGILDIR (bkz. docs/ops/MONITORING_STACK_RUNBOOK.md "Promotion criteria from pilot to enforced").

