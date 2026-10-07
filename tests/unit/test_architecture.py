"""Architecture tests — the dependency rule, enforced.

The brief states the rule twice (§7, §10.6): imports point inward, and the
domain imports nothing from the outer layers. Its practical test is *"you could
replace FastAPI, or swap pgvector for Chroma, and `domain/` would not change by
a single line."*

A rule that lives only in a document erodes on the first busy afternoon. These
tests parse the source with `ast` — no imports executed, so they are fast and
cannot themselves be fooled by import side effects — and fail loudly the moment
a framework leaks into the wrong layer.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Set

import pytest

PROJECT = Path(__file__).resolve().parents[2]
SRC = PROJECT / "src" / "sentinel"

DOMAIN = SRC / "domain"
REPOSITORIES = SRC / "repositories"

# Packages the domain must never reach for. Anything that would tie business
# concepts to a delivery mechanism or a storage engine.
FORBIDDEN_IN_DOMAIN = {
    "sqlalchemy",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langchain_groq",
    "langchain_huggingface",
    "langgraph",
    "chromadb",
    "psycopg",
    "redis",
    "httpx",
    "requests",
    "reportlab",
    "yfinance",
    "tavily",
}

# The concrete tool modules. Only the registry may import them (objective M-04):
# every other caller goes through `registry.invoke`, which is what guarantees the
# audit trail is complete rather than best-effort.
TOOL_MODULES = {
    "sentinel.tools.edgar",
    "sentinel.tools.finance",
    "sentinel.tools.osint",
    "sentinel.tools.rag",
    "sentinel.tools.sanctions",
}
REGISTRY_MODULE = "registry.py"

# Only the gateway may speak to a model provider (objective M-01).
PROVIDER_SDKS = {"langchain_groq", "langchain_ollama", "groq", "openai",
                 "langchain_openai", "huggingface_hub"}
# Only the adapters may touch a provider SDK. Named individually rather than by
# directory so that adding a file to `llm/` does not silently widen the exemption.
LLM_GATEWAY_MODULES = {"groq_adapter.py", "ollama_adapter.py", "huggingface_adapter.py"}


def python_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def imported_roots(path: Path) -> Set[str]:
    """Top-level package names imported by a module, via AST — nothing executed."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Relative imports have no module root to police.
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])

    return roots


def imported_modules(path: Path) -> Set[str]:
    """Fully qualified module names imported by a module, via AST.

    `imported_roots` collapses to the top-level package, which cannot tell
    `sentinel.tools.registry` from `sentinel.tools.sanctions`. This one keeps the
    dotted path so a boundary *inside* a package can be policed.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)

    return modules


@pytest.mark.skipif(not DOMAIN.is_dir(), reason="domain package not present")
@pytest.mark.parametrize("path", list(python_files(DOMAIN)), ids=lambda p: p.name)
def test_domain_imports_no_framework(path: Path) -> None:
    """The domain depends on the standard library and itself, nothing else."""
    leaked = imported_roots(path) & FORBIDDEN_IN_DOMAIN
    assert not leaked, (
        f"{path.relative_to(SRC)} imports {sorted(leaked)}. The domain must stay "
        "framework-free so storage and delivery remain replaceable details."
    )


@pytest.mark.skipif(not DOMAIN.is_dir(), reason="domain package not present")
@pytest.mark.parametrize("path", list(python_files(DOMAIN)), ids=lambda p: p.name)
def test_domain_imports_only_itself_within_the_project(path: Path) -> None:
    """The domain never reaches into another sentinel package."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = set()

    for node in ast.walk(tree):
        module = None
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("sentinel."):
                    module = alias.name
        if module and module.startswith("sentinel.") and not module.startswith("sentinel.domain"):
            offenders.add(module)

    assert not offenders, (
        f"{path.relative_to(SRC)} imports {sorted(offenders)}. Dependencies point "
        "inward: outer layers may import the domain, never the reverse."
    )


def shipped_modules() -> Iterator[Path]:
    """Every module that is part of the product, including root-level entry points.

    Scanning only `src/` would let a stray script at the repository root import a
    provider directly and still pass — which is exactly the leak this guards.
    """
    yield from python_files(SRC)
    for path in sorted(PROJECT.glob("*.py")):
        yield path
    if (PROJECT / "scripts").is_dir():
        yield from python_files(PROJECT / "scripts")


@pytest.mark.skipif(not (SRC / "services").is_dir(), reason="services package not present")
def test_only_the_gateway_imports_a_provider_sdk() -> None:
    """M-01: no provider SDK may be imported outside the LLM gateway."""
    offenders = {}

    for path in shipped_modules():
        if path.name in LLM_GATEWAY_MODULES:
            continue
        leaked = imported_roots(path) & PROVIDER_SDKS
        if leaked:
            offenders[str(path.relative_to(PROJECT))] = sorted(leaked)

    assert not offenders, (
        "Provider SDKs must only be imported by the LLM gateway, otherwise the "
        f"provider-agnostic boundary is fiction. Leaks: {offenders}"
    )


def test_only_the_registry_imports_a_tool_module() -> None:
    """M-04: every tool call goes through the registry, so every call is recorded.

    A specialist that imports `tools.sanctions` and calls it directly produces a
    screening that appears nowhere in the audit trail. The verdict would still
    cite it, and nothing would reveal that the citation rests on an unrecorded
    call — which is precisely the traceability gap M-04 exists to close.

    This test found two such modules when it was written: `agents/compliance_agent.py`
    and `agents/financial_agent.py`, both dead code, one of them no longer even
    importable. They are retired in `docs/legacy/`.
    """
    offenders = {}

    for path in shipped_modules():
        if path.name == REGISTRY_MODULE or path.parent.name == "tools":
            continue
        leaked = imported_modules(path) & TOOL_MODULES
        if leaked:
            offenders[str(path.relative_to(PROJECT))] = sorted(leaked)

    assert not offenders, (
        "Tool modules must only be imported by the registry; a direct call is a "
        f"call that never reaches the audit trail. Leaks: {offenders}"
    )


@pytest.mark.skipif(not REPOSITORIES.is_dir(), reason="repositories package not present")
def test_repository_interface_is_storage_agnostic() -> None:
    """The contract itself must not mention a storage engine."""
    leaked = imported_roots(REPOSITORIES / "interfaces.py") & FORBIDDEN_IN_DOMAIN
    assert not leaked, (
        f"repositories/interfaces.py imports {sorted(leaked)}. The contract is "
        "written in domain language; only its implementations know about SQL."
    )
