from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "verify_vercel_packaging.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_vercel_packaging", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_valid_policy(root: Path) -> None:
    module = _load_module()
    (root / "frontend").mkdir(parents=True)
    (root / "backend").mkdir(parents=True)
    (root / ".gitignore").write_text(
        "\n".join(sorted(module.REQUIRED_GITIGNORE_PATTERNS)) + "\n",
        encoding="utf-8",
    )
    (root / ".vercelignore").write_text(
        "\n".join(module.ROOT_VERCEL_PATTERNS) + "\n",
        encoding="utf-8",
    )
    (root / "frontend" / ".vercelignore").write_text(
        "\n".join(module.FRONTEND_VERCEL_PATTERNS) + "\n",
        encoding="utf-8",
    )
    (root / "backend" / ".vercelignore").write_text(
        "\n".join(module.BACKEND_VERCEL_PATTERNS) + "\n",
        encoding="utf-8",
    )


def test_repository_packaging_policy_is_valid() -> None:
    module = _load_module()

    assert module.validate_repository(REPO_ROOT) == []


def test_missing_rule_is_reported_without_reading_secret_contents(tmp_path: Path) -> None:
    module = _load_module()
    _write_valid_policy(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8").replace(".git-credentials\n", ""),
        encoding="utf-8",
    )
    secret = tmp_path / ".git-credentials"
    secret.write_text("do-not-disclose", encoding="utf-8")

    violations = module.validate_repository(tmp_path, tracked_files=[])

    assert violations == [".gitignore is missing required pattern: .git-credentials"]
    assert all("do-not-disclose" not in violation for violation in violations)


def test_gitignore_policy_covers_environment_variants_and_keeps_templates() -> None:
    module = _load_module()

    assert ".env.*" in module.REQUIRED_GITIGNORE_PATTERNS
    assert "!.env.example" in module.REQUIRED_GITIGNORE_PATTERNS
    assert "!.env.sample" in module.REQUIRED_GITIGNORE_PATTERNS
    assert "!.env.template" in module.REQUIRED_GITIGNORE_PATTERNS


def test_tracked_sensitive_paths_are_rejected(tmp_path: Path) -> None:
    module = _load_module()
    _write_valid_policy(tmp_path)

    violations = module.validate_repository(
        tmp_path,
        tracked_files=[
            "frontend/src/app/page.tsx",
            "nested/.netrc",
            "certificates/deploy.pem",
            ".env.example",
            "frontend/.env.example",
        ],
    )

    assert violations == [
        "tracked sensitive path: certificates/deploy.pem",
        "tracked sensitive path: nested/.netrc",
    ]


def test_unreviewed_project_allowlist_is_rejected(tmp_path: Path) -> None:
    module = _load_module()
    _write_valid_policy(tmp_path)
    frontend_ignore = tmp_path / "frontend" / ".vercelignore"
    frontend_ignore.write_text("/*\n!src\n", encoding="utf-8")

    violations = module.validate_repository(tmp_path, tracked_files=[])

    assert "frontend/.vercelignore has unreviewed pattern: /*" in violations


def test_project_policies_never_ignore_the_entire_traversal_root() -> None:
    module = _load_module()

    for patterns in (
        module.FRONTEND_VERCEL_PATTERNS,
        module.BACKEND_VERCEL_PATTERNS,
    ):
        assert "/*" not in patterns
        assert not any(pattern.startswith("!") for pattern in patterns)
        assert module.COMMON_PROJECT_VERCEL_PATTERNS.issubset(patterns)
