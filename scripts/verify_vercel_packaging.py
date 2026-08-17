from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath


REQUIRED_GITIGNORE_PATTERNS = frozenset(
    {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        "*.cer",
        "*.crt",
        "*.jks",
        "*.key",
        "*.keystore",
        "*.p12",
        "*.pem",
        "*.pfx",
        "credentials.json",
        "id_ed25519*",
        "id_rsa*",
    }
)

ROOT_VERCEL_PATTERNS = (
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".env",
    ".env.*",
    "*.cer",
    "*.crt",
    "*.jks",
    "*.key",
    "*.keystore",
    "*.p12",
    "*.pem",
    "*.pfx",
    "credentials.json",
    "id_ed25519*",
    "id_rsa*",
    ".worktrees/",
    ".superpowers/",
    ".ruff_cache/",
    "docs/",
    "tools/",
    "scripts/",
    "**/.pytest_cache/",
    "**/__pycache__/",
    "**/.coverage",
    "**/htmlcov/",
    "**/node_modules/",
    "**/.next/",
    "**/coverage/",
    "**/playwright-report/",
    "**/test-results/",
    "**/*.tsbuildinfo",
)

FRONTEND_VERCEL_PATTERNS = (
    "/*",
    "!public",
    "!src",
    "!eslint.config.mjs",
    "!next.config.ts",
    "!package-lock.json",
    "!package.json",
    "!postcss.config.mjs",
    "!tsconfig.json",
)

BACKEND_VERCEL_PATTERNS = (
    "/*",
    "!api",
    "!coordination",
    "!domain",
    "!repositories",
    "!.python-version",
    "!__init__.py",
    "!airtable_client.py",
    "!ai_service.py",
    "!auth.py",
    "!config.py",
    "!main.py",
    "!requirements.txt",
    "!upstash_url.py",
    "!vercel.json",
    "!write_gates.py",
)

_SENSITIVE_FILENAMES = frozenset(
    {
        ".git-credentials",
        ".netrc",
        ".npmrc",
        "credentials.json",
        "settings.json",
    }
)
_SENSITIVE_SUFFIXES = frozenset(
    {".cer", ".crt", ".jks", ".key", ".keystore", ".p12", ".pem", ".pfx"}
)
_ENV_EXAMPLES = frozenset({".env.example", ".env.sample", ".env.template"})


def _patterns(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    return tuple(
        line
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    )


def _validate_exact_policy(
    path: Path,
    expected: Sequence[str],
    display_path: str,
    *,
    require_allowlist: bool = False,
) -> list[str]:
    actual = _patterns(path)
    if not actual:
        return [f"missing deployment policy: {display_path}"]

    violations: list[str] = []
    if require_allowlist and actual[0] != "/*":
        violations.append(f"{display_path} must start with /*")

    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected))
    violations.extend(f"{display_path} is missing required pattern: {item}" for item in missing)
    violations.extend(f"{display_path} has unreviewed pattern: {item}" for item in unexpected)
    return violations


def _tracked_files(repo_root: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return tuple(
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    )


def _is_sensitive_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    if name in _SENSITIVE_FILENAMES or ".vercel" in parts:
        return True
    if name.startswith(".env") and name not in _ENV_EXAMPLES:
        return True
    if name.startswith("id_rsa") or name.startswith("id_ed25519"):
        return True
    return path.suffix.lower() in _SENSITIVE_SUFFIXES


def validate_repository(
    repo_root: Path,
    *,
    tracked_files: Iterable[str] | None = None,
) -> list[str]:
    repo_root = repo_root.resolve()
    violations: list[str] = []

    gitignore_patterns = set(_patterns(repo_root / ".gitignore"))
    for pattern in sorted(REQUIRED_GITIGNORE_PATTERNS - gitignore_patterns):
        violations.append(f".gitignore is missing required pattern: {pattern}")

    violations.extend(
        _validate_exact_policy(
            repo_root / ".vercelignore",
            ROOT_VERCEL_PATTERNS,
            ".vercelignore",
        )
    )
    violations.extend(
        _validate_exact_policy(
            repo_root / "frontend" / ".vercelignore",
            FRONTEND_VERCEL_PATTERNS,
            "frontend/.vercelignore",
            require_allowlist=True,
        )
    )
    violations.extend(
        _validate_exact_policy(
            repo_root / "backend" / ".vercelignore",
            BACKEND_VERCEL_PATTERNS,
            "backend/.vercelignore",
            require_allowlist=True,
        )
    )

    candidates = _tracked_files(repo_root) if tracked_files is None else tracked_files
    violations.extend(
        f"tracked sensitive path: {path}"
        for path in sorted({item.replace("\\", "/") for item in candidates})
        if _is_sensitive_path(path)
    )
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        violations = validate_repository(repo_root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Packaging policy check could not run: {type(exc).__name__}", file=sys.stderr)
        return 2

    if violations:
        print("Vercel packaging policy violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print("Vercel packaging policy is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
