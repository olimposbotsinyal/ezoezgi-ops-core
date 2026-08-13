Gerçek Prometheus v3.13.2 + Alertmanager v0.33.1 ile Gate D dogrulamasi.
Sadece for: sureleri kisaltildi (test hizi icin) -- expr/labels/annotations
ve alertmanager.yml (routing/receiver'lar) REPODAKI GERCEK dosyalarla
BIREBIR AYNI (yalniz webhook URL placeholder'lari yerel dummy URL'lerle
degistirildi, config validation icin -- gercek route hala null-receiver).
2026-08-13T07:29:46+00:00
