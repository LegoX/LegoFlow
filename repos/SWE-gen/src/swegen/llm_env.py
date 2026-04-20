from __future__ import annotations

import os


def _normalize_openai_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url
    return f"{url}/v1"


def _normalize_anthropic_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url[:-3]
    return url


def hydrate_cross_provider_env() -> None:
    """Mirror LLM env vars so OpenAI/Anthropic formats can both drive the repo."""
    openai_key = os.getenv("OPENAI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if not openai_key and anthropic_key:
        os.environ["OPENAI_API_KEY"] = anthropic_key
    elif openai_key and not anthropic_key:
        os.environ["ANTHROPIC_API_KEY"] = openai_key

    openai_base = os.getenv("OPENAI_API_BASE_URL") or os.getenv("OPENAI_API_BASE")
    anthropic_base = os.getenv("ANTHROPIC_BASE_URL")
    normalized_openai = _normalize_openai_base_url(openai_base)
    normalized_anthropic = _normalize_anthropic_base_url(anthropic_base)

    if not normalized_openai and normalized_anthropic:
        normalized_openai = _normalize_openai_base_url(normalized_anthropic)
    if not normalized_anthropic and normalized_openai:
        normalized_anthropic = _normalize_anthropic_base_url(normalized_openai)

    if normalized_openai:
        os.environ["OPENAI_API_BASE_URL"] = normalized_openai
        os.environ["OPENAI_API_BASE"] = normalized_openai
    if normalized_anthropic:
        os.environ["ANTHROPIC_BASE_URL"] = normalized_anthropic


def get_openai_compatible_config(model: str | None = None) -> tuple[str | None, str | None, str | None]:
    hydrate_cross_provider_env()
    return (
        model or os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL"),
        os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        os.getenv("OPENAI_API_BASE_URL") or os.getenv("OPENAI_API_BASE"),
    )


def get_anthropic_compatible_config(model: str | None = None) -> tuple[str | None, str | None, str | None]:
    hydrate_cross_provider_env()
    return (
        model or os.getenv("ANTHROPIC_MODEL") or os.getenv("OPENAI_MODEL"),
        os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"),
        os.getenv("ANTHROPIC_BASE_URL"),
    )
