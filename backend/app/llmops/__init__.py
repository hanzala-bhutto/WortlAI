"""LLMOps: the seam between the app and Langfuse.

Two independent concerns, one behind each file, so Langfuse is a single swappable
dependency and never a hard runtime dependency:

- prompts.py  - fetch versioned prompts, with a bundled in-repo fallback so agents
                have their instructions even when Langfuse is gone entirely.
- tracing.py  - record agent/LLM calls, as a no-op when Langfuse is unconfigured
                or unreachable, so tracing can never break a session.

Both share one Langfuse client from client.py, created only when keys are set.
"""
