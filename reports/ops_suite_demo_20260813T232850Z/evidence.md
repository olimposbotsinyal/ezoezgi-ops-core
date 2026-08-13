# Ops Suite E2E Demo Kaniti

Uretildi (UTC): 2026-08-13T23:28:50.183526+00:00
base_url: http://127.0.0.1:8420
Genel sonuc: **PASS**

## server_startup -- OK

```json
{
  "base_url": "http://127.0.0.1:8420"
}
```

## voice_command_echo -- OK

```json
{
  "request": {
    "request_id": "404d2083-96c6-4f56-91d2-6883276d8ff4",
    "tr_response": "Merhaba yazdırıldı.",
    "result_en": {
      "value": "Merhaba",
      "status": "ok",
      "task_en": "RUN_ECHO",
      "risk_level": "low"
    }
  }
}
```

## voice_command_irreversible -- OK

```json
{
  "response": {
    "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
    "tr_response": "Bu işlem yüksek riskli (irreversible) olduğu için onay bekliyor. Devam etmek için onay vermeniz gerekiyor.",
    "result_en": {
      "status": "WAITING_APPROVAL",
      "task_en": "RUN_DELETE_FILE",
      "risk_level": "irreversible"
    }
  }
}
```

## approvals_pending_check -- OK

```json
{
  "pending": [
    {
      "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
      "alias": "ezo",
      "task": "RUN_DELETE_FILE",
      "risk_level": "irreversible",
      "original_tr": "Ezo, tüm dosyaları sil",
      "submitted_at": "2026-08-13T23:28:52.077039+00:00",
      "details": {
        "confidence": 0.6
      }
    }
  ]
}
```

## approve_rejected_without_token -- OK

```json
{
  "status_code": 401,
  "response": {
    "detail": "kimlik dogrulama token'i saglanmadi (Authorization: Bearer <token> bekleniyor)"
  }
}
```

## approve_rejected_delegate_root_guard -- OK

```json
{
  "status_code": 403,
  "response": {
    "detail": "'ops_suite_demo_delegate' (delegate) risk_level='irreversible' onayi SAHIBI-ONLY bir kok (root) eylemdir -- delegate kapsami GECERSIZDIR"
  },
  "note": "delegate config'inde approve:irreversible OLMASINA RAGMEN root-guard reddetti"
}
```

## approvals_still_pending_after_denied_attempts -- OK

```json
{
  "pending": [
    {
      "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
      "alias": "ezo",
      "task": "RUN_DELETE_FILE",
      "risk_level": "irreversible",
      "original_tr": "Ezo, tüm dosyaları sil",
      "submitted_at": "2026-08-13T23:28:52.077039+00:00",
      "details": {
        "confidence": 0.6
      }
    }
  ]
}
```

## approve_decision -- OK

```json
{
  "response": {
    "record_type": "DECIDED",
    "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
    "timestamp": "2026-08-13T23:28:52.099914+00:00",
    "decision": "approved",
    "actor_id": "ops_suite_demo_owner",
    "auth_method": "bearer",
    "authority_source": "owner",
    "decision_scope": "owner_root",
    "note": "E2E demo onayi (scripts/ops_suite_demo.py)"
  }
}
```

## approvals_cleared_check -- OK

```json
{
  "pending_after": []
}
```

## audit_log_check -- OK

```json
{
  "matching_records": [
    {
      "timestamp": "2026-08-13T23:28:52.077547+00:00",
      "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
      "alias": "ezo",
      "task": "RUN_DELETE_FILE",
      "risk_level": "irreversible",
      "status": "WAITING_APPROVAL",
      "details": {
        "original_tr": "Ezo, tüm dosyaları sil",
        "confidence": 0.6,
        "result_en": {
          "status": "WAITING_APPROVAL",
          "task_en": "RUN_DELETE_FILE",
          "risk_level": "irreversible"
        }
      }
    },
    {
      "timestamp": "2026-08-13T23:28:52.100215+00:00",
      "request_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
      "alias": null,
      "task": null,
      "risk_level": "irreversible",
      "status": "APPROVED",
      "details": {
        "actor_id": "ops_suite_demo_owner",
        "auth_method": "bearer",
        "authority_source": "owner",
        "decision_scope": "owner_root",
        "note": "E2E demo onayi (scripts/ops_suite_demo.py)",
        "source": "ops_suite_approval_endpoint"
      }
    }
  ]
}
```

## agents_snapshot -- OK

```json
{
  "agents": [
    {
      "agent_id": "orchestrator",
      "display_name": "Orchestrator",
      "state": "idle",
      "last_heartbeat_ts": "2026-08-13T23:28:52.077534+00:00",
      "last_task_id": "9678f89f-35dc-4937-bcd2-e9e07aaabecf",
      "detail": "",
      "updated_at": "2026-08-13T23:28:52.129511+00:00"
    },
    {
      "agent_id": "bridge_agent",
      "display_name": "Bridge Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "henuz heartbeat alinmadi",
      "updated_at": ""
    },
    {
      "agent_id": "tool_runners",
      "display_name": "Tool Runners",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "henuz heartbeat alinmadi",
      "updated_at": ""
    },
    {
      "agent_id": "finance_agent",
      "display_name": "Finance Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    },
    {
      "agent_id": "social_agent",
      "display_name": "Social Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    },
    {
      "agent_id": "research_agent",
      "display_name": "Research Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    },
    {
      "agent_id": "doc_agent",
      "display_name": "Doc Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    },
    {
      "agent_id": "device_agent",
      "display_name": "Device Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    },
    {
      "agent_id": "voice_agent",
      "display_name": "Voice Agent",
      "state": "offline",
      "last_heartbeat_ts": null,
      "last_task_id": null,
      "detail": "not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
      "updated_at": ""
    }
  ]
}
```

## NOT_COLLECTED

Bu ortamda gercek donanim/tarayici olmadigi icin ASLA fabrike EDILMEDI:

- **real_browser_rendering**: bu ortamda tarayici-otomasyon araci YOK -- bkz. docs/BACKLOG.md B039
- **real_microphone_speaker_audio**: ses donanimi (mikrofon/hoparlor/gercek TTS) bu ortamda YOK -- bkz. docs/BACKLOG.md B040
- **real_gsm_sim_call_flow**: GSM modem/SIM donanimi YOK, services/gsm-gateway hala bos -- bkz. docs/BACKLOG.md B040/B043
- **real_camera_gesture_input**: kamera donanimi YOK, services/gesture-vision hala bos -- bkz. docs/BACKLOG.md B040
