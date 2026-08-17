# backend/infrastructure/repositories/policy_repository.py
"""策略仓储 —— 电话回访策略快照的持久化（说明书 5.4 / 附录 A）。

进程内实现用于演示；多实例可替换为共享存储。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.models.callback_policy import CallbackPolicy, CallbackPolicySnapshot


class PolicyRepository:
    """电话回访策略快照仓储（进程内，演示用）。"""

    def __init__(self):
        self._store: dict[str, CallbackPolicySnapshot] = {}
        self._by_text: dict[str, str] = {}

    @staticmethod
    def _hash(raw_text: str, compiled: dict) -> str:
        return hashlib.sha256(
            (raw_text + json.dumps(compiled, sort_keys=True, ensure_ascii=False)).encode("utf-8")
        ).hexdigest()[:16]

    def save_policy(self, policy: CallbackPolicy, approved_by: str = "",
                    approved_at: str = "") -> CallbackPolicySnapshot:
        compiled = policy.model_dump()
        version = f"cb-{self._hash(policy.source_text, compiled)}"
        snap = CallbackPolicySnapshot(
            policy_version=version,
            raw_text=policy.source_text,
            compiled_json=compiled,
            hash=policy.hash or version,
            approved_by=approved_by,
            approved_at=approved_at,
            status="approved",
        )
        self._store[version] = snap
        self._by_text[policy.source_text] = version
        return snap

    def get_snapshot(self, version: str) -> CallbackPolicySnapshot | None:
        return self._store.get(version)

    def find_by_text(self, raw_text: str) -> CallbackPolicySnapshot | None:
        v = self._by_text.get(raw_text)
        if v is None:
            return None
        return self._store.get(v)

    def list_all(self) -> list[CallbackPolicySnapshot]:
        return list(self._store.values())
