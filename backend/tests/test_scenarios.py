"""The scenario registry: the fixed set of roleplays a session can start.

A scenario is data (persona, level, opening line, Redemittel), not a prompt - the
Tutor's instructions come from Langfuse and take these as variables. The registry
is what the /scenarios picker lists and what the graph's setup node reads, so the
contract that matters is: every scenario is well formed, ids are unique, and an
unknown id fails loudly rather than starting a session with no persona.
"""

from dataclasses import FrozenInstanceError

import pytest

from app.agents.scenarios import (
    CEFR_LEVELS,
    get_scenario,
    list_scenarios,
)


def test_registry_is_non_empty_and_covers_the_a1_b1_range():
    scenarios = list_scenarios()
    # A small hardcoded set for Phase 1: enough to practise across the band,
    # not so many that hardcoding them stops being reasonable.
    assert 5 <= len(scenarios) <= 8
    levels = {s.level for s in scenarios}
    assert levels <= set(CEFR_LEVELS)
    # Hanzala starts at A1 and is climbing to B1, so the whole A1->B1 band must
    # be represented - a session has somewhere to start and somewhere to grow.
    assert {"A1", "A2", "B1"} <= levels


def test_scenario_ids_are_unique():
    ids = [s.id for s in list_scenarios()]
    assert len(ids) == len(set(ids))


def test_every_scenario_is_well_formed():
    for s in list_scenarios():
        assert s.id and s.id.islower()
        assert s.title
        assert s.persona
        assert s.opening_line
        assert s.level in CEFR_LEVELS
        # Redemittel are the chunks the Tutor should elicit; a scenario without
        # any is a content bug, since the whole point is chunk-based practice.
        assert s.redemittel


def test_get_scenario_returns_the_registered_one():
    first = list_scenarios()[0]
    assert get_scenario(first.id) is first


def test_get_unknown_scenario_raises():
    with pytest.raises(KeyError):
        get_scenario("no-such-scenario")


def test_scenario_is_immutable():
    s = list_scenarios()[0]
    with pytest.raises(FrozenInstanceError):
        s.level = "C2"  # frozen dataclass


def test_no_scenario_string_would_trip_the_inline_prompt_tripwire():
    # scenarios.py lives under app/agents/, which is scanned for long string
    # literals (test_agents_have_no_inline_prompts). Scenario copy is data, but it
    # must stay short enough not to look like an inlined prompt.
    for s in list_scenarios():
        for field in (s.title, s.persona, s.opening_line, *s.redemittel):
            assert len(field) <= 200
