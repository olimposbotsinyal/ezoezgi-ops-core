# Go-Live Gate Raporu -- Model Gateway Observability

Uretildi (UTC): 2026-08-13T07:31:18.528944+00:00
Calisma modu: simulation
Genel sonuc: **PASS** (exit code 0)

| Gate | Durum | Aciklama |
|---|---|---|
| A_metrics_availability | PASS | SIMULASYON modu (kisa ornek pencere, GERCEK 24s gozlem DEGIL) [10 ornek x 0.2s] -- 10/10 basarili (kullanilabilirlik=100.00%) |
| B_scrape_success_rate | PASS | SIMULASYON (yerel tekrar istek) -- scrape basari orani=100.00% (20/20), esik >=%99 |
| C_synthetic_alerts_visible | PASS | 4/4 sentetik mod /metrics'te gorunur oldu |
| D_alertmanager_receive_path | PASS | Alertmanager alma yolu uctan uca dogrulandi |
| E_classify_regression_smoke | PASS | classify-ilgili regresyon testleri exit_code=0 -- ============================= 54 passed in 3.71s ============================== |

## Gate kanitlari (ham veri)

### A_metrics_availability
```json
{
  "successes": 10,
  "samples": 10,
  "availability": 1.0,
  "simulated": true,
  "window_label": "10 ornek x 0.2s"
}
```

### B_scrape_success_rate
```json
{
  "successes": 20,
  "samples": 20,
  "rate": 1.0,
  "simulated": true
}
```

### C_synthetic_alerts_visible
```json
{
  "mode_visibility": {
    "preflight-unknown": true,
    "circuit-open-stuck": true,
    "fallback-spike": true,
    "null-intent-spike": true
  },
  "visible": [
    "preflight-unknown",
    "circuit-open-stuck",
    "fallback-spike",
    "null-intent-spike"
  ],
  "missing": []
}
```

### D_alertmanager_receive_path
```json
{
  "alertmanager_installed": true,
  "alertmanager_reachable": true,
  "alert_received": true
}
```

### E_classify_regression_smoke
```json
{
  "pytest_exit_code": 0,
  "tests_summary": "============================= 54 passed in 3.71s =============================="
}
```

