from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "secret-scan.yml"


def test_secret_scan_runs_for_pushes_and_pull_requests_with_pinned_action() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s*push:\s*$", workflow)
    assert re.search(r"(?m)^\s*pull_request:\s*$", workflow)
    assert re.search(r"uses:\s*gitleaks/gitleaks-action@[0-9a-f]{40}\b", workflow)
    assert "fetch-depth: 0" in workflow
    assert "contents: read" in workflow
    assert "GITLEAKS_CONFIG: .gitleaks.toml" in workflow
    assert "GITLEAKS_VERSION: 8.30.0" in workflow


def test_all_external_workflow_actions_are_pinned_to_commits() -> None:
    workflows = (REPO_ROOT / ".github" / "workflows").glob("*.yml")
    uses = [
        match.group(1)
        for path in workflows
        for match in re.finditer(r"(?m)^\s*-\s+uses:\s*([^\s#]+)", path.read_text(encoding="utf-8"))
    ]

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)
