# Güvenlik Sertleştirme Sprint-1 -- Gerçek Kanıt (B051/B052/B053, PLAN.md T50/T51/T52)

Üretildi (UTC): 2026-08-14T02:56:54.680052+00:00
Genel sonuç: **PASS**

## NOT_COLLECTED

Bu sprint tamamen backend/kütüphane seviyesindedir (frontend/tarayıcı yok) -- toplanamayan bir kanıt YOK.

## Adımlar

### server_startup -- OK

```json
{
  "base_url": "http://127.0.0.1:8430"
}
```

### owner_whoami -- OK

```json
{
  "status_code": 200,
  "response": {
    "actor_id": "sec_demo_owner",
    "display_name": "Sec-Sprint Demo Owner (yalniz bu kosum icin -- GERCEK bir kisi DEGIL)",
    "authority_source": "owner"
  }
}
```

### rotate_delegate_token -- OK

```json
{
  "status_code": 200,
  "response": {
    "actor_id": "sec_demo_delegate"
  }
}
```

### old_delegate_token_rejected_after_rotation -- OK

```json
{
  "status_code": 401,
  "response": {
    "detail": "token iptal edilmis (revoked)"
  }
}
```

### new_delegate_token_works -- OK

```json
{
  "status_code": 200,
  "response": {
    "actor_id": "sec_demo_delegate",
    "display_name": "Sec-Sprint Demo Delegate (yalniz bu kosum icin -- GERCEK bir kisi DEGIL)",
    "authority_source": "delegate"
  }
}
```

### delegate_cannot_rotate_owner_only_guard -- OK

```json
{
  "status_code": 403,
  "response": {
    "detail": "'sec_demo_delegate' owner degil -- rotate SAHIBI-ONLY bir islemdir"
  }
}
```

### identity_admin_rate_limit_triggered -- OK

```json
{
  "status_code": 429,
  "response": {
    "detail": {
      "reason_code": "RATE_LIMITED",
      "message": "'identity_admin' icin istek sikligi siniri asildi",
      "retry_after_seconds": 59.8
    }
  }
}
```

### audit_log_contains_all_standardized_reason_codes -- OK

```json
{
  "expected": [
    "AUTHZ_OWNER_ONLY",
    "AUTH_TOKEN_REVOKED",
    "OK",
    "RATE_LIMITED"
  ],
  "seen": [
    "AUTHZ_OWNER_ONLY",
    "AUTH_TOKEN_REVOKED",
    "OK",
    "RATE_LIMITED"
  ],
  "total_records": 23
}
```

### audit_log_contains_no_raw_token_values -- OK

```json
{}
```

### revocation_log_persists_hash_not_raw_token -- OK

```json
{}
```
