# TR-EN Bridge — Harici AI (External AI) Politikası

> Durum: v0, 2026-08-14 — kod referansı:
> `services/model-gateway/src/model_gateway/policy.py`,
> `services/model-gateway/src/model_gateway/config.py`.

## 1. Yerel-öncelikli (local-first) ilke

EzoEzgi, MASTER_ROADMAP.md §1/§6'da tanımlandığı gibi **offline-first**
bir sistemdir. TR-EN bridge'in NLU sınıflandırması (`services/tr-en-bridge/src/bridge.py::_classify`)
varsayılan olarak `mock` sağlayıcıyı kullanır; `NLU_PROVIDER=ollama`
ayarlandığında bile, sonuç YEREL bir Ollama sunucusuna gider —
`model_gateway`'in `remote` sağlayıcısı (harici bir AI API'si, örneğin
Claude Code iş akışları) **VARSAYILAN OLARAK KAPALIDIR**:

```python
# model_gateway/config.py
remote_enabled: bool = False        # REMOTE_ENABLED env degiskeniyle acilir
remote_policy_gate: str = "required" # REMOTE_POLICY_GATE
```

```python
# model_gateway/policy.py::is_remote_allowed
# policies/*.yaml'daki remote_policy.allowed alani varsayilan olarak False --
# dosya yok/bozuk/bolum eksikse dahi GUVENLI varsayilan (allowed=False) doner.
```

Bu, kullanıcı isteğinde belirtilen "TR->EN external AI bridge (including
Claude Code workflows)" hedefinin **kapıları KAPALI, açıkça
yapılandırılmadıkça devre dışı** bir şekilde var olduğu anlamına gelir
— Ops Suite bu politikayı GEVŞETMEZ/BAYPAS ETMEZ.

## 2. Ops Suite'in bu politikayla ilişkisi

**Ops Suite v0, `model_gateway`'in remote sağlayıcısını HİÇ
tetiklemez** — `voice_bridge.py`, `bridge.translate_and_extract()`'i
DOĞRUDAN çağırır; bu da yalnızca `NLU_PROVIDER` ortam değişkenine göre
`mock` veya (yerel) `ollama` kullanır. `scripts/ops_suite_demo.py`'nin
gerçek çalıştırmasında `NLU_PROVIDER` ayarlanmadı — yani demo, mock
sınıflandırıcıyı kullandı (bkz. `reports/ops_suite_demo_<UTC>/evidence.json`'daki
`confidence` alanları, mock'un sabit güven değerleriyle eşleşir).

## 3. "Uzak AI kullanılıyor" rozeti — v0'da YOK

Ürün spesifikasyonu (`OPS_SUITE_PRODUCT_SPEC.md`) kasıtlı olarak v0'da
bir "şu an uzak bir AI kullanılıyor" görsel rozeti İÇERMEZ — çünkü **v0
kapsamında hiçbir akış gerçekten uzak bir AI'ya bağlanmıyor** (yukarıya
bkz.). Böyle bir rozet eklemek, kullanılmayan bir özelliği fabrike
etmiş olurdu. `remote_enabled=True` VE gerçek bir remote çağrısı
yapıldığı bir senaryo GELECEKTE oluşursa, bu rozet o zaman
`assistant_presence.py`'ye (örneğin `AssistantPresenceEvent`'e yeni bir
`using_remote_ai: bool` alanı olarak) eklenmelidir — bkz.
`docs/BACKLOG.md`.

## 4. Denetlenebilirlik

`model_gateway`'in kendi audit/metrics katmanı (bkz.
`docs/RUNBOOK.md` "Model Gateway" bölümü) zaten HANGİ sağlayıcının
(`ollama`/`local_alt`/`remote`) kullanıldığını kaydeder — Ops Suite bu
mekanizmayı DUPLICATE ETMEZ, yalnızca `data/audit/audit.log.jsonl`
üzerinden (kendi `VoiceBridge` katmanının ürettiği kayıtlar) kendi
görev-seviyesi izini tutar. İki günlük (audit.log.jsonl ve
model-gateway'in kendi metrik günlüğü) BİRBİRİNDEN BAĞIMSIZDIR, aynı
`request_id` ile çapraz-referanslanmaz (v0 sınırlaması).
