from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class FollowUpChain:
    root_question_id: str
    followup_chain_id: str
    probe_reason: str
    probe_depth: int = 0
    resolved: bool = False
    semantic_topic: str = ""
    repeat_count: int = 0
    probe_type: str = "clarification_probe"
    last_probe_question: str = ""
    last_probe_intent: str = ""
    last_score: int | None = None
    resolution_reason: str = ""
    termination_reason: str = ""

    @classmethod
    def from_dict(cls, payload: Any) -> FollowUpChain | None:
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                root_question_id=str(payload.get("root_question_id") or ""),
                followup_chain_id=str(payload.get("followup_chain_id") or ""),
                probe_reason=str(payload.get("probe_reason") or ""),
                probe_depth=int(payload.get("probe_depth") or 0),
                resolved=bool(payload.get("resolved", False)),
                semantic_topic=str(payload.get("semantic_topic") or ""),
                repeat_count=int(payload.get("repeat_count") or 0),
                probe_type=str(payload.get("probe_type") or "clarification_probe"),
                last_probe_question=str(payload.get("last_probe_question") or ""),
                last_probe_intent=str(payload.get("last_probe_intent") or ""),
                last_score=cls._optional_int(payload.get("last_score")),
                resolution_reason=str(payload.get("resolution_reason") or ""),
                termination_reason=str(payload.get("termination_reason") or ""),
            )
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


@dataclass(slots=True)
class FollowUpMemory:
    active_chain_id: str | None = None
    chains: list[FollowUpChain] = field(default_factory=list)
    semantic_probes: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Any) -> FollowUpMemory:
        if not isinstance(payload, dict):
            return cls()
        chains: list[FollowUpChain] = []
        for item in payload.get("chains", []):
            chain = FollowUpChain.from_dict(item)
            if chain is not None and chain.followup_chain_id:
                chains.append(chain)
        semantic_probes = [
            dict(item)
            for item in payload.get("semantic_probes", [])
            if isinstance(item, dict)
        ]
        active_chain_id = payload.get("active_chain_id")
        active = str(active_chain_id) if active_chain_id else None
        return cls(active_chain_id=active, chains=chains, semantic_probes=semantic_probes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_chain_id": self.active_chain_id,
            "chains": [chain.to_dict() for chain in self.chains],
            "semantic_probes": [dict(item) for item in self.semantic_probes[-24:]],
        }

    def active_chain(self) -> FollowUpChain | None:
        if not self.active_chain_id:
            return None
        for chain in self.chains:
            if chain.followup_chain_id == self.active_chain_id:
                return chain
        return None

    def set_active_chain(self, chain: FollowUpChain | None) -> None:
        self.active_chain_id = chain.followup_chain_id if chain else None

    def upsert_chain(self, chain: FollowUpChain) -> None:
        for index, existing in enumerate(self.chains):
            if existing.followup_chain_id == chain.followup_chain_id:
                self.chains[index] = chain
                return
        self.chains.append(chain)
