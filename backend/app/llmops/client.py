"""The one place a Langfuse client is constructed.

Returns None when Langfuse keys are absent - the degraded state /health already
reports - so prompts fall back to the bundled copies and tracing goes no-op,
rather than the app failing to boot over an optional dependency. The client is
cached: prompt caching and the OpenTelemetry span exporter both live inside it,
so a single shared instance is what we want.
"""

from functools import lru_cache

from langfuse import Langfuse

from app.config import Settings, get_settings


def _build(settings: Settings) -> Langfuse | None:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )


@lru_cache
def get_langfuse() -> Langfuse | None:
    """The shared Langfuse client, or None when unconfigured."""
    return _build(get_settings())
