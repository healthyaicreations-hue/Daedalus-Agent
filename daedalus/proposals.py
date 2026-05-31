"""Daedalus Proposals — code change proposal system.

A proposal is a pending code change that a human can approve or reject.
Approved proposals write the new content to disk.

Storage keys: "daedalus:proposal:{id}"
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .storage import Storage, default_storage


@dataclass
class Proposal:
    id: str
    file_path: str
    new_content: str
    reason: str
    proposed_by: str = "daedalus"
    status: str = "pending"     # pending | approved | rejected
    created_at: float = field(default_factory=time.time)
    lint_warnings: list[dict] = field(default_factory=list)
    sandbox_result: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_path": self.file_path,
            "new_content": self.new_content,
            "reason": self.reason,
            "proposed_by": self.proposed_by,
            "status": self.status,
            "created_at": self.created_at,
            "lint_warnings": self.lint_warnings,
            "sandbox_result": self.sandbox_result,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Proposal":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ProposalStore:
    """Create, list, approve, and reject proposals."""

    def __init__(self, storage: Storage | None = None) -> None:
        self._storage = storage or default_storage()

    def _key(self, proposal_id: str) -> str:
        return f"daedalus:proposal:{proposal_id}"

    def create(
        self,
        file_path: str,
        new_content: str,
        reason: str,
        proposed_by: str = "daedalus",
        lint_warnings: list[dict] | None = None,
        sandbox_result: dict | None = None,
        metadata: dict | None = None,
    ) -> Proposal:
        """Create and store a new proposal."""
        proposal = Proposal(
            id=str(uuid.uuid4())[:8],
            file_path=file_path,
            new_content=new_content,
            reason=reason,
            proposed_by=proposed_by,
            lint_warnings=lint_warnings or [],
            sandbox_result=sandbox_result or {},
            metadata=metadata or {},
        )
        self._storage.set(self._key(proposal.id), proposal.to_dict())
        return proposal

    def get(self, proposal_id: str) -> Proposal | None:
        data = self._storage.get(self._key(proposal_id))
        if data is None:
            return None
        return Proposal.from_dict(data)

    def list_pending(self) -> list[Proposal]:
        keys = self._storage.keys("daedalus:proposal:")
        proposals = []
        for key in keys:
            data = self._storage.get(key)
            if data and data.get("status") == "pending":
                proposals.append(Proposal.from_dict(data))
        return sorted(proposals, key=lambda p: p.created_at, reverse=True)

    def list_all(self) -> list[Proposal]:
        keys = self._storage.keys("daedalus:proposal:")
        proposals = []
        for key in keys:
            data = self._storage.get(key)
            if data:
                proposals.append(Proposal.from_dict(data))
        return sorted(proposals, key=lambda p: p.created_at, reverse=True)

    def approve(self, proposal_id: str, write_to_disk: bool = True) -> dict[str, Any]:
        """Approve a proposal. Optionally writes new_content to disk."""
        proposal = self.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal {proposal_id} not found"}
        if proposal.status != "pending":
            return {"ok": False, "error": f"Proposal {proposal_id} is already {proposal.status}"}

        if write_to_disk:
            path = Path(proposal.file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(proposal.new_content, encoding="utf-8")

        proposal.status = "approved"
        self._storage.set(self._key(proposal_id), proposal.to_dict())
        return {"ok": True, "proposal_id": proposal_id, "file_path": proposal.file_path}

    def reject(self, proposal_id: str, reason: str = "") -> dict[str, Any]:
        """Reject a proposal without writing to disk."""
        proposal = self.get(proposal_id)
        if proposal is None:
            return {"ok": False, "error": f"Proposal {proposal_id} not found"}
        proposal.status = "rejected"
        if reason:
            proposal.metadata["rejection_reason"] = reason
        self._storage.set(self._key(proposal_id), proposal.to_dict())
        return {"ok": True, "proposal_id": proposal_id}

    def validate_and_create(
        self,
        file_path: str,
        new_content: str,
        reason: str,
        proposed_by: str = "daedalus",
        test_code: str = "",
        run_lint: bool = True,
        run_sandbox: bool = True,
    ) -> dict[str, Any]:
        """Run lint + sandbox, then create proposal if checks pass.

        Returns:
            {ok, proposal_id?, error?, lint_gate_blocked?, sandbox_failed?}
        """
        from .lint import lint_content
        from .sandbox import validate_patch

        lint_warnings: list[dict] = []
        sandbox_result: dict = {}

        if run_lint:
            lint_r = lint_content(file_path, new_content)
            if not lint_r.ok:
                return {
                    "ok": False,
                    "lint_gate_blocked": True,
                    "error": f"Lint gate blocked: {len(lint_r.blockers)} blocker(s)",
                    "blockers": [b.to_dict() if hasattr(b, 'to_dict') else vars(b)
                                 for b in lint_r.blockers],
                }
            lint_warnings = lint_r.to_dict().get("warnings", [])

        if run_sandbox:
            sandbox_result = validate_patch(file_path, new_content, test_code=test_code)
            if not sandbox_result.get("ok"):
                return {
                    "ok": False,
                    "sandbox_failed": True,
                    "error": sandbox_result.get("blocking_reason", "sandbox failed"),
                    "sandbox": sandbox_result,
                }

        proposal = self.create(
            file_path=file_path,
            new_content=new_content,
            reason=reason,
            proposed_by=proposed_by,
            lint_warnings=lint_warnings,
            sandbox_result=sandbox_result,
        )
        return {"ok": True, "proposal_id": proposal.id}
