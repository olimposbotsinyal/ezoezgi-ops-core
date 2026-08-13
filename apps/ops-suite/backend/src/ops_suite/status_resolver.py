"""Ajan durum cozumleyicisi (PLAN.md T22) -- `HeartbeatTracker` + audit
gecmisini birlestirip nihai `AgentPresence` listesini uretir.

**Durustluk ilkesi (bkz. docs/AGENT_PRESENCE_STATE_MODEL.md):**
`MASTER_ROADMAP.md §3`'te tanimli ama HENUZ GERCEK calisan kodu OLMAYAN
ajanlar (Finance/Social/Research/Doc/Device/Voice Agent -- servis
klasorleri hala bos, bkz. `services/*-engine`, `services/gsm-gateway`,
`services/stt-whisper` vb.) HICBIR ZAMAN "idle"/"working" olarak
GOSTERILMEZ -- `state="offline"`, `detail="not_implemented"` ile ACIKCA
isaretlenir. Bu, bir ajanin sessizce calisiyormus GIBI GORUNMESINI
(fabrike edilmis bir "canlilik" izlenimini) ONLER."""

from __future__ import annotations

from ops_suite.audit_tail import AuditTailReader
from ops_suite.heartbeat import HeartbeatTracker
from ops_suite.schemas import AgentPresence

# Gercek, calisan koda sahip ajanlar (bkz. MASTER_ROADMAP.md §3 -- Servis
# sutunu asagidaki yollarda GERCEKTEN kod barindiriyor).
KNOWN_LIVE_AGENTS: dict[str, str] = {
    "orchestrator": "Orchestrator",
    "bridge_agent": "Bridge Agent",
    "tool_runners": "Tool Runners",
}

# MASTER_ROADMAP.md §3'te tanimli AMA servis klasoru hala bos olan
# (`services/*` icinde yalnizca `.gitkeep`) ajanlar -- bkz. yukaridaki
# durustluk ilkesi.
NOT_IMPLEMENTED_AGENTS: dict[str, str] = {
    "finance_agent": "Finance Agent",
    "social_agent": "Social Agent",
    "research_agent": "Research Agent",
    "doc_agent": "Doc Agent",
    "device_agent": "Device Agent",
    "voice_agent": "Voice Agent",
}


class AgentStatusResolver:
    def __init__(self, heartbeat_tracker: HeartbeatTracker, audit_tail_reader: AuditTailReader | None = None) -> None:
        self._heartbeat = heartbeat_tracker
        self._audit = audit_tail_reader

    def resolve_all(self) -> list[AgentPresence]:
        """Doner: (1) en az bir kez heartbeat atmis ajanlarin GERCEK anlik
        goruntusu, (2) `KNOWN_LIVE_AGENTS`'te olup HENUZ hic heartbeat
        atmamis ajanlar icin `offline`/"henuz heartbeat yok" durumu, (3)
        `NOT_IMPLEMENTED_AGENTS`'in TAMAMI icin `offline`/`not_implemented`
        -- SESSIZCE atlanan/eksik hicbir bilinen ajan YOKTUR."""
        heartbeat_snapshot = {p.agent_id: p for p in self._heartbeat.snapshot()}
        result: list[AgentPresence] = []

        for agent_id, display_name in KNOWN_LIVE_AGENTS.items():
            if agent_id in heartbeat_snapshot:
                result.append(heartbeat_snapshot.pop(agent_id))
            else:
                result.append(
                    AgentPresence(
                        agent_id=agent_id, display_name=display_name, state="offline",
                        detail="henuz heartbeat alinmadi",
                    )
                )

        # Bilinmeyen (ne KNOWN_LIVE_AGENTS ne NOT_IMPLEMENTED_AGENTS'te
        # olan) ama heartbeat atmis bir ajan varsa (ornegin ileride
        # eklenecek yeni bir ajan), YINE DE GOSTERILIR -- SESSIZCE
        # kaybolmaz.
        result.extend(heartbeat_snapshot.values())

        for agent_id, display_name in NOT_IMPLEMENTED_AGENTS.items():
            result.append(
                AgentPresence(
                    agent_id=agent_id, display_name=display_name, state="offline",
                    detail="not_implemented -- bkz. MASTER_ROADMAP.md §3, servis klasoru henuz bos",
                )
            )

        return result
