"""The fixed set of roleplay scenarios a session can start.

A Scenario is *data*, not a prompt: the Tutor's instructions live in the prompt
store (Langfuse + bundled fallback) and take a scenario's persona, level and
Redemittel as variables. Keeping the copy here as short data fields (never a
200+ char blob) also keeps it clear of the no-inline-prompt tripwire that scans
this package.

The set is deliberately small and hardcoded for Phase 1 - everyday Dresden
situations from A1 (where Hanzala starts) up through B1. /new-scenario (#...
later) is where adding one becomes a scaffolded workflow, and scenarios can move
to data (DB/config, or Curriculum-generated) once there are enough to warrant it;
for now a new entry is a code change with a test, which is enough at this size.

A scenario is a *practice context*, not the vocabulary store: breadth of
vocabulary comes from ingestion (Phase 2, the lexical graph + FSRS deck), so the
count of scenarios does not cap what can be learned - the same handful of
scenarios activate an ever-growing deck.

Each scenario has exactly one `persona` today (a 1:1 dialogue). Multi-persona
scenes (several counterparts in one scene) are a noted future extension - see
docs/PLAN.md - so nothing here should assume "exactly one other speaker" in a way
that blocks adding more.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The CEFR rungs a Phase 1 scenario can sit on. Pinned so a typo ("A3") fails a
# test rather than reaching the Tutor prompt as an unresolvable level.
CEFR_LEVELS = ("A1", "A2", "B1", "B2")


@dataclass(frozen=True)
class Scenario:
    """One roleplay. The Tutor plays the counterpart described by `persona` and
    opens the conversation with `opening_line`; `redemittel` are the chunks the
    scenario is meant to elicit (what FSRS cards get built from later)."""

    id: str
    title: str  # German, shown in the picker
    level: str  # CEFR rung from CEFR_LEVELS
    persona: str  # who the Tutor plays and the setting, fed to the prompt
    opening_line: str  # the Tutor's first German turn
    redemittel: tuple[str, ...] = field(default_factory=tuple)


# Order here is the order the picker shows. Kept easiest-first (A1 -> B1) so a
# session can start gently. Every string stays well under the inline-prompt limit.
_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="vorstellen",
        title="Sich vorstellen",
        level="A1",
        persona="Du lernst jemanden neu kennen und stellst einfache Fragen: Name, "
        "Herkunft, Wohnort, Sprachen. Sprich langsam und sehr einfach.",
        opening_line="Hallo! Ich heiße Lena. Wie heißt du?",
        redemittel=(
            "Ich heiße …",
            "Ich komme aus …",
            "Ich wohne in Dresden.",
            "Ich spreche ein bisschen Deutsch.",
        ),
    ),
    Scenario(
        id="supermarkt",
        title="Im Supermarkt",
        level="A1",
        persona="Du arbeitest an der Kasse im Supermarkt. Der Kunde legt Waren aufs "
        "Band und bezahlt. Nenne Zahlen und Preise klar und langsam.",
        opening_line="Hallo! Haben Sie eine Payback-Karte? Das macht dann acht Euro.",
        redemittel=(
            "Wie viel kostet das?",
            "Ich bezahle mit Karte.",
            "Eine Tüte, bitte.",
            "Hier, bitte.",
        ),
    ),
    Scenario(
        id="uhrzeit",
        title="Nach der Uhrzeit fragen",
        level="A1",
        persona="Du bist ein freundlicher Passant. Jemand fragt dich nach der "
        "Uhrzeit und wie spät es ist. Antworte einfach und kurz.",
        opening_line="Ja, bitte? Kann ich Ihnen helfen?",
        redemittel=(
            "Wie spät ist es?",
            "Es ist halb drei.",
            "Vielen Dank!",
            "Entschuldigung, wissen Sie …?",
        ),
    ),
    Scenario(
        id="baeckerei",
        title="In der Bäckerei",
        level="A2",
        persona="Du bist die Verkäuferin in einer Bäckerei in Dresden. Der Kunde "
        "möchte Brot und Brötchen kaufen. Bleib freundlich und einfach.",
        opening_line="Guten Morgen! Was darf es sein?",
        redemittel=(
            "Ich hätte gern …",
            "Was kostet …?",
            "Zwei Brötchen, bitte.",
            "Sonst noch etwas?",
        ),
    ),
    Scenario(
        id="cafe",
        title="Im Café bestellen",
        level="A2",
        persona="Du bist Kellner in einem Café. Der Gast setzt sich und möchte "
        "etwas trinken und vielleicht ein Stück Kuchen bestellen.",
        opening_line="Hallo, schön dass Sie da sind. Möchten Sie schon bestellen?",
        redemittel=(
            "Ich nehme …",
            "Einen Kaffee, bitte.",
            "Könnte ich die Karte haben?",
            "Zusammen oder getrennt?",
        ),
    ),
    Scenario(
        id="nachbar",
        title="Small Talk mit dem Nachbarn",
        level="A2",
        persona="Du bist ein netter Nachbar im Treppenhaus. Ihr trefft euch kurz "
        "und macht Small Talk über das Wetter und den Alltag.",
        opening_line="Ah, hallo! Na, auch gerade unterwegs? Schönes Wetter heute, oder?",
        redemittel=(
            "Wie geht es Ihnen?",
            "Ja, wirklich schön.",
            "Ich muss noch einkaufen.",
            "Bis bald!",
        ),
    ),
    Scenario(
        id="arzttermin",
        title="Termin beim Arzt",
        level="B1",
        persona="Du bist die/der Sprechstundenhilfe in einer Arztpraxis. Der "
        "Patient ruft an, um einen Termin zu machen und sagt, was ihm fehlt.",
        opening_line="Praxis Dr. Wagner, guten Tag. Was kann ich für Sie tun?",
        redemittel=(
            "Ich hätte gern einen Termin.",
            "Mir tut … weh.",
            "Passt Ihnen Dienstag?",
            "Bringen Sie Ihre Versichertenkarte mit.",
        ),
    ),
    Scenario(
        id="wohnung",
        title="Wohnungsbesichtigung",
        level="B1",
        persona="Du bist Vermieter und zeigst eine Wohnung in Dresden. Der "
        "Interessent stellt Fragen zu Miete, Zimmern und Einzugstermin.",
        opening_line="Willkommen! Kommen Sie rein, ich zeige Ihnen die Wohnung. "
        "Was möchten Sie zuerst sehen?",
        redemittel=(
            "Wie hoch ist die Miete?",
            "Ist die Wohnung möbliert?",
            "Ab wann ist sie frei?",
            "Sind Haustiere erlaubt?",
        ),
    ),
)

# Indexed once at import; ids are unique (a test guards this).
_BY_ID: dict[str, Scenario] = {s.id: s for s in _SCENARIOS}


def list_scenarios() -> list[Scenario]:
    """Every registered scenario, in picker order."""
    return list(_SCENARIOS)


def get_scenario(scenario_id: str) -> Scenario:
    """The scenario with this id, or KeyError - a session must not start with an
    unknown scenario and no persona to pin the Tutor to."""
    try:
        return _BY_ID[scenario_id]
    except KeyError:
        raise KeyError(
            f"Unknown scenario {scenario_id!r}. Known: {sorted(_BY_ID)}"
        ) from None
