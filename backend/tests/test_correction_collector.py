"""Contract for CorrectorCollector: the fire-and-forget runner around the Corrector.

The two properties that make the staged-correction design hold, pinned here:

- submit() returns without waiting on the analysis (AC #1): a turn spends zero time
  on error detection, which runs concurrently and is gathered only at the debrief.
- collect() drains every outstanding analysis, filters by the config severity
  threshold (AC #3), and swallows a failed task instead of raising (guardrail #4).
"""

import asyncio

from app.agents.corrector import CorrectorCollector, ErrorReport


def report(severity, error_type="grammar.case.dative"):
    return ErrorReport(
        error_type=error_type,
        severity=severity,
        utterance="mit der Hund",
        correction="mit dem Hund",
        explanation="Nach mit steht der Dativ.",
    )


class FakeCorrector:
    """Returns queued report-lists per call, recording the utterances it saw."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.seen = []

    async def analyze(self, *, utterance, context=None):
        self.seen.append(utterance)
        return self._batches.pop(0)


class GatedCorrector:
    """Blocks inside analyze() until released, so a test can prove submit() does not
    wait on the analysis and collect() does."""

    def __init__(self, batch):
        self._batch = batch
        self.gate = asyncio.Event()
        self.started = asyncio.Event()

    async def analyze(self, *, utterance, context=None):
        self.started.set()
        await self.gate.wait()
        return self._batch


class BoomCorrector:
    """Analyze always raises, standing in for every provider being down."""

    async def analyze(self, *, utterance, context=None):
        raise RuntimeError("all providers failed")


async def test_submit_does_not_wait_on_the_analysis(tmp_path):
    corrector = GatedCorrector([report("critical")])
    collector = CorrectorCollector(corrector)

    collector.submit(1, utterance="Ich gehe mit der Hund.")
    # The turn is free to continue: the analysis has not been awaited. Yield once so
    # the task can start, then assert it is parked at the gate, not finished.
    await corrector.started.wait()
    assert corrector.gate.is_set() is False  # still blocked; submit did not await it

    corrector.gate.set()
    rows = await collector.collect(1)
    assert len(rows) == 1  # collect() is where we actually wait for the result


async def test_collect_drains_all_turns_for_a_session(tmp_path):
    corrector = FakeCorrector([[report("critical")], [], [report("critical")]])
    collector = CorrectorCollector(corrector)

    for utterance in ("u1", "u2", "u3"):
        collector.submit(7, utterance=utterance)
    rows = await collector.collect(7)

    assert len(rows) == 2  # two turns had a critical error, one was clean
    assert corrector.seen == ["u1", "u2", "u3"]


async def test_collect_applies_the_severity_threshold(tmp_path):
    corrector = FakeCorrector([[report("critical"), report("minor")]])
    collector = CorrectorCollector(corrector, severity_threshold="critical")

    collector.submit(1, utterance="Ich gehe mit der Hund.")
    rows = await collector.collect(1)

    # Early weeks: only the communication-breaking error reaches the debrief.
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"
    assert rows[0]["explanation"] == "Nach mit steht der Dativ."


async def test_collect_below_threshold_lets_everything_through(tmp_path):
    corrector = FakeCorrector([[report("critical"), report("minor")]])
    collector = CorrectorCollector(corrector, severity_threshold="minor")

    collector.submit(1, utterance="Ich gehe mit der Hund.")
    rows = await collector.collect(1)

    assert len(rows) == 2  # later weeks surface minor errors too


async def test_collect_swallows_a_failed_task(tmp_path):
    collector = CorrectorCollector(BoomCorrector())

    collector.submit(1, utterance="Ich gehe mit der Hund.")
    rows = await collector.collect(1)

    assert rows == []  # a dead analysis is dropped, never raised into the debrief


async def test_collect_isolates_sessions(tmp_path):
    corrector = FakeCorrector([[report("critical")], [report("critical")]])
    collector = CorrectorCollector(corrector)

    collector.submit(1, utterance="a")
    collector.submit(2, utterance="b")

    rows1 = await collector.collect(1)
    assert len(rows1) == 1
    # Draining session 1 leaves session 2 untouched.
    rows2 = await collector.collect(2)
    assert len(rows2) == 1
    # A session with nothing submitted collects cleanly.
    assert await collector.collect(99) == []


async def test_discard_drops_the_session_so_a_later_collect_sees_nothing(tmp_path):
    """#48: a client that disconnects without `end` never reaches debrief, so the
    voice loop discards instead of collecting. Pinning that discard pops the entry
    just like collect does, so `_tasks` doesn't grow unbounded across abandoned
    sessions."""
    corrector = FakeCorrector([[report("critical")]])
    collector = CorrectorCollector(corrector)

    collector.submit(1, utterance="a")
    collector.discard(1)

    assert 1 not in collector._tasks
    # A later, unrelated collect for the same id sees nothing left to gather.
    assert await collector.collect(1) == []


async def test_discard_on_an_unknown_session_is_a_no_op(tmp_path):
    collector = CorrectorCollector(FakeCorrector([]))
    collector.discard(999)  # must not raise


async def test_discard_swallows_a_failed_tasks_exception(tmp_path, caplog):
    """Without discard consuming the exception, a failed task with no reader would
    log "Task exception was never retrieved" at garbage collection instead of the
    clear message discard's callback produces."""
    collector = CorrectorCollector(BoomCorrector())

    collector.submit(1, utterance="Ich gehe mit der Hund.")
    collector.discard(1)
    # Two yields: one lets the submitted task itself run (and raise), the second
    # lets asyncio's own call_soon-scheduled done_callback dispatch fire after
    # that - a single yield only covers the first.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "abandoned session" in caplog.text
