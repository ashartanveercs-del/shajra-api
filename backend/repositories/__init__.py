"""Append-only persistence contracts and local repository implementations."""

from repositories.memory import InMemoryGraphRepository, RepositoryCorruptionError
from repositories.protocols import (
    AuditOperation,
    AuditOperationState,
    CommitPermit,
    GraphCommit,
    GraphRepository,
    GraphWriteSet,
    StagedWriteReceipt,
    WriteContext,
    canonical_graph_commit_json,
    canonical_graph_write_set_json,
    graph_commit_sha256,
)

__all__ = [
    "AuditOperation",
    "AuditOperationState",
    "CommitPermit",
    "GraphCommit",
    "GraphRepository",
    "GraphWriteSet",
    "InMemoryGraphRepository",
    "RepositoryCorruptionError",
    "StagedWriteReceipt",
    "WriteContext",
    "canonical_graph_commit_json",
    "canonical_graph_write_set_json",
    "graph_commit_sha256",
]
