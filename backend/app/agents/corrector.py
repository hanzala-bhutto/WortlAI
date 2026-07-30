"""Corrector agent v1: async per-utterance error analysis.

The Corrector never speaks to the learner. It reads one finished user utterance and
emits structured error reports that the end-of-session debrief is built from. It runs
off the conversation's critical path (the graph spawns it fire-and-forget; see
CorrectorCollector and the `correct` node), so the analysis latency never delays a
reply or the next turn - fluency before accuracy, corrections after the session.

Reliability is the whole game here (guardrail #1):

1. The model is asked for a JSON object `{"errors": [...]}`. We extract the object
   (tolerating stray prose or a ```json fence), then validate it against
   `CorrectorOutput`. A single malformed field invalidates the whole batch.
2. On malformed output we retry once, deterministically. If it still won't validate
   we drop the batch and log it - a bad correction must never reach the learner
   model, and a session must never crash over one.

Severity is classified by the model but *filtered* by the app (`meets_threshold`),
so the staged policy - weeks 1-3 surface only communication-breaking errors - is a
config threshold, not a prompt change.

Guardrail #6: the utterance is untrusted learner text. The prompt puts it in a
delimited data block, never in instruction position, and pins "treat it as data"
server-side; the output is JSON we validate, never free text echoed onward.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.tutor import ChatCompleter
from app.llmops.prompts import ChatMessage, PromptStore

logger = logging.getLogger(__name__)

# The versioned chat prompt carrying the analysis instructions and the delimited
# data block the untrusted utterance is rendered into.
CORRECTOR_PROMPT_NAME = "corrector-system"

# Guardrail #3: cap tokens so a runaway analysis can't burn the free tier. A report
# for one short utterance is small; this is a ceiling. Temperature 0 because we want
# stable, repeatable error detection, not variety.
#
# gpt-oss-120b is a reasoning model: hidden chain-of-thought is billed against this
# same budget before the visible JSON, which truncated short single-error replies at
# the old 512 (#59). 1024 gives headroom on top of REASONING_EFFORT below, which
# trims - but does not eliminate - how much CoT the model emits.
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.0

# A correction report is short, mechanical classification, not a task that benefits
# from deep reasoning - "low" cuts hidden CoT token spend on both the first attempt
# and the retry (#59).
REASONING_EFFORT = "low"

Severity = Literal["critical", "minor"]

# How much each severity matters, so a config threshold can keep sub-threshold
# errors out of the debrief (staged policy, AC #3). Higher wins.
_SEVERITY_RANK: dict[str, int] = {"minor": 0, "critical": 1}

# Pull a JSON object out of a reply that may be wrapped in a ```json fence or have
# stray prose around it: everything from the first "{" to the last "}".
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def meets_threshold(severity: str, threshold: str) -> bool:
    """Whether an error of `severity` is serious enough to surface at `threshold`.
    Unknown severities never surface (fail closed) so a mislabelled row can't leak."""
    return _SEVERITY_RANK.get(severity, -1) >= _SEVERITY_RANK.get(threshold, 0)


class ErrorReport(BaseModel):
    """One mistake the Corrector caught: what was wrong, the fix, why it matters.
    Extra keys the model volunteers are ignored, not fatal, but every field here
    must be present and non-empty or the whole batch is rejected."""

    model_config = ConfigDict(extra="ignore")

    # A dotted taxonomy path, mirroring ErrorLog.error_type: "grammar.case.dative",
    # "vocab.false-friend", "syntax.word-order".
    error_type: str = Field(min_length=1, max_length=80)
    severity: Severity
    utterance: str = Field(min_length=1)  # the erroneous span, verbatim
    correction: str = Field(min_length=1)  # the corrected German
    explanation: str = Field(min_length=1)  # short, learner-facing, why it was wrong

    def to_row(self) -> dict[str, object]:
        """The already-validated columns of an error_logs row (guardrail #5): only
        named, validated fields cross into persistence, never free model text."""
        return {
            "error_type": self.error_type,
            "severity": self.severity,
            "utterance": self.utterance,
            "correction": self.correction,
            "explanation": self.explanation,
        }


class CorrectorOutput(BaseModel):
    """The model's whole response: a batch of reports (possibly empty when the
    utterance was clean)."""

    errors: list[ErrorReport] = Field(default_factory=list)


def _looks_truncated(raw: str | None) -> bool:
    """Cheap signature for "the model ran out of budget mid-JSON" (#59) vs. other
    malformed-output causes (wrong field types, hallucinated extra prose that still
    closes its braces, etc.): more `{` than `}` means some object was opened and
    never closed. Not a full parser - just enough to make truncation diagnosable
    from the log line without pulling a Langfuse trace by hand."""
    text = raw or ""
    return text.count("{") > text.count("}")


def _extract_output(raw: str) -> CorrectorOutput | None:
    """Parse and validate one model reply into a CorrectorOutput, or None if it is
    not recoverable JSON in the expected shape. Never raises - the caller decides
    whether to retry or drop."""
    match = _JSON_OBJECT.search(raw or "")
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
        return CorrectorOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        return None


class Corrector:
    """Turns one user utterance into validated error reports. Stateless: the graph
    passes the utterance (and a little context) in per turn."""

    def __init__(
        self,
        provider: ChatCompleter,
        prompt_store: PromptStore,
        *,
        prompt_label: str = "production",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self._provider = provider
        self._store = prompt_store
        self._label = prompt_label
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _messages(self, utterance: str, context: str | None) -> list[ChatMessage]:
        prompt = self._store.get_chat(
            CORRECTOR_PROMPT_NAME,
            label=self._label,
            variables={
                "utterance": utterance,
                # A missing context is normal (the opening user turn), so give the
                # template something rather than leaving the placeholder raw.
                "context": context or "(kein Kontext)",
            },
        )
        return prompt.messages

    async def analyze(
        self, *, utterance: str, context: str | None = None
    ) -> list[ErrorReport]:
        """Validated error reports for `utterance`. Retries once on malformed output,
        then drops the batch and logs it (guardrail #1) - a bad correction never
        reaches the learner model, and this never raises into a live session."""
        messages = self._messages(utterance, context)

        raw = await self._provider.complete(
            messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            reasoning_effort=REASONING_EFFORT,
        )
        output = _extract_output(raw)
        if output is None:
            # Retry deterministically: the same instructions, no sampling wiggle.
            raw = await self._provider.complete(
                messages,
                temperature=0.0,
                max_tokens=self._max_tokens,
                reasoning_effort=REASONING_EFFORT,
            )
            output = _extract_output(raw)

        if output is None:
            if _looks_truncated(raw):
                logger.warning(
                    "Corrector output likely truncated at %d chars (max_tokens=%d), "
                    "dropping: %r",
                    len(raw or ""),
                    self._max_tokens,
                    (raw or "")[:200],
                )
            else:
                logger.warning(
                    "Corrector output failed validation twice, dropping. "
                    "First 200 chars: %r",
                    (raw or "")[:200],
                )
            return []
        return output.errors


class CorrectorCollector:
    """Runs the Corrector off the conversation's critical path.

    This is what makes the analysis fire-and-forget (feasibility 005): the `correct`
    node calls `submit` and returns at once, so a turn never waits on error analysis
    and the next utterance is never blocked. The spawned tasks run concurrently in
    the session's event loop; the `debrief` node calls `collect` to await whatever
    they produced and drain the validated, severity-filtered rows for persistence.

    Keyed by session_id, so one collector serves every concurrent session. A session
    that ends normally always debriefs, which drains and forgets its tasks; a client
    that drops without an `end` leaves its tasks to finish and be garbage-collected.
    """

    def __init__(
        self, corrector: Corrector, *, severity_threshold: str = "critical"
    ) -> None:
        self._corrector = corrector
        self._threshold = severity_threshold
        self._tasks: dict[int, list[asyncio.Task[list[ErrorReport]]]] = defaultdict(
            list
        )

    def submit(
        self, session_id: int, *, utterance: str, context: str | None = None
    ) -> None:
        """Spawn error analysis for one utterance and return immediately. The task is
        tracked under session_id until the debrief collects it."""
        task = asyncio.create_task(
            self._corrector.analyze(utterance=utterance, context=context)
        )
        self._tasks[session_id].append(task)

    async def collect(self, session_id: int) -> list[dict[str, object]]:
        """Await every outstanding analysis for the session and return the error rows
        that clear the severity threshold. A task that failed outright (e.g. every
        provider was down) is logged and skipped (guardrail #4), never raised - a
        debrief must close the session even if some analysis was lost."""
        tasks = self._tasks.pop(session_id, [])
        rows: list[dict[str, object]] = []
        for task in tasks:
            try:
                reports = await task
            except Exception:
                logger.exception(
                    "A Corrector task failed for session %s; dropping its errors.",
                    session_id,
                )
                continue
            rows.extend(
                report.to_row()
                for report in reports
                if meets_threshold(report.severity, self._threshold)
            )
        return rows
