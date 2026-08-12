# EzoEzgi Ops

Local-first, çok ajanlı bir operasyon asistanı. Kullanıcıyla Türkçe konuşur,
iç işleyişte (planlama, tool-call, ajan-arası mesajlaşma) İngilizce çalışır.

- Vizyon, mimari ve faz planı: [docs/MASTER_ROADMAP.md](docs/MASTER_ROADMAP.md)
- Aktif sprint (ilk 14 gün): [docs/PLAN.md](docs/PLAN.md)
- Ertelenen/gelecek işler: [docs/BACKLOG.md](docs/BACKLOG.md)
- Mimari kararlar (ADR): [docs/DECISIONS.md](docs/DECISIONS.md)
- Operasyonel başvuru (kurulum, komutlar, sorun giderme): [docs/RUNBOOK.md](docs/RUNBOOK.md)
- Faz 0 kapanış özeti: [docs/releases/PHASE0_CLOSURE.md](docs/releases/PHASE0_CLOSURE.md)
- Asistan kimliği/alias config: [config/assistant.identity.json](config/assistant.identity.json)

## Durum

**Faz 0 kapandı** (bkz. PLAN.md, T5–T14 + T17), **Faz 1 sürüyor** — mock TR
sınıflandırıcının yanına gerçek bir Ollama NLU adaptörü eklendi (B031,
`NLU_PROVIDER=ollama` ile etkinleştirilir, varsayılan hâlâ `mock`). Finans
işlem yürütme (T18–T20) bilinçli olarak Faz 2'ye ertelendi — detay
[docs/releases/PHASE0_CLOSURE.md](docs/releases/PHASE0_CLOSURE.md) ve
[docs/RUNBOOK.md](docs/RUNBOOK.md) → "Faz 1 — Ollama NLU Entegrasyonu"'da.

## Çalıştırma (Hızlı Başlangıç)

```
cd D:\Projects\ezoezgi-ops
C:\Python313\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip pytest pyyaml

.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\e2e_demo.py "Ezo, echo ile 'merhaba' yaz"
```

Beklenen çıktı: `"Merhaba yazdırıldı."` + `data/audit/audit.log.jsonl`'a bir
kayıt. Detaylı adımlar, ikinci (onay bekleyen) senaryo ve bilinen ortam
notları için bkz. [docs/RUNBOOK.md](docs/RUNBOOK.md) → "E2E Demo Çalıştırma".

## Repo Isolation

Bu repo, kök dizinindeki [`PROJECT_IDENTITY.yaml`](PROJECT_IDENTITY.yaml)
manifestiyle kimliklendirilir (`repo_slug: ezoezgi-asaf-core`) — amacı, bu
çalışma alanının başka bir proje/checkout ile yanlışlıkla karışmasını
önlemek. Yeni bir görev/scripte başlamadan önce şu komutla doğrulanabilir:

```
.\.venv\Scripts\python.exe scripts\preflight.py
```

Detay için bkz. [docs/RUNBOOK.md](docs/RUNBOOK.md) → "Preflight Kontrol".
`PROJECT_IDENTITY.yaml` elle silinmemeli/taşınmamalı.
