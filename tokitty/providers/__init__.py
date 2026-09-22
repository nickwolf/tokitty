"""Provider registry.

Kinds are matched exactly, never guessed at. An accounts.json written by a
newer Tokitty can name a provider this build has never heard of, and
quietly falling back to Claude Code there would poll the wrong harness and
show numbers belonging to a different account -- worse than saying so.
"""
from __future__ import annotations

from typing import Dict, Tuple

from tokitty.providers.base import LedgerSource, NullProvider, Provider, ProviderCapabilities, STATUS_UNSUPPORTED
from tokitty.providers.claude import ClaudeProvider
from tokitty.providers.codex import CodexProvider

DEFAULT_KIND = "claude"

NULL_PROVIDER = NullProvider()

_REGISTRY: Dict[str, Provider] = {
    ClaudeProvider.kind: ClaudeProvider(),
    CodexProvider.kind: CodexProvider(),
}


class UnknownProviderError(KeyError):
    """Raised for a provider kind this build does not implement."""


def supported_kinds() -> Tuple[str, ...]:
    return tuple(_REGISTRY)


def get_provider(kind: str = DEFAULT_KIND) -> Provider:
    """The provider for a kind. An empty or missing kind means Claude Code
    -- that is what every accounts.json written before the seam existed
    describes."""
    resolved = (kind or DEFAULT_KIND).strip().lower()
    try:
        return _REGISTRY[resolved]
    except KeyError:
        raise UnknownProviderError(
            f"unknown provider {kind!r}; this build supports {', '.join(supported_kinds())}"
        ) from None


__all__ = [
    "DEFAULT_KIND",
    "LedgerSource",
    "NULL_PROVIDER",
    "NullProvider",
    "STATUS_UNSUPPORTED",
    "Provider",
    "ProviderCapabilities",
    "UnknownProviderError",
    "get_provider",
    "supported_kinds",
]
