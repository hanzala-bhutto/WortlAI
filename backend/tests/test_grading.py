"""The one mapping from conversation-use signal to an FSRS grade (#14).

Grades come from how a chunk was used, not a flashcard tap: unprompted is Good
(Easy on a repeated unprompted use), a gloss is Hard, a failure or an avoidance
is Again.
"""

from fsrs import Rating

from app.learner.grading import EASY_UNPROMPTED_USES, Signal, signal_to_rating


def test_unprompted_single_use_is_good():
    assert signal_to_rating(Signal.UNPROMPTED) is Rating.Good


def test_repeated_unprompted_use_is_easy():
    assert signal_to_rating(Signal.UNPROMPTED, EASY_UNPROMPTED_USES) is Rating.Easy


def test_unprompted_below_threshold_is_good():
    assert signal_to_rating(Signal.UNPROMPTED, EASY_UNPROMPTED_USES - 1) is Rating.Good


def test_glossed_is_hard():
    assert signal_to_rating(Signal.GLOSSED) is Rating.Hard


def test_failed_is_again():
    assert signal_to_rating(Signal.FAILED) is Rating.Again


def test_avoided_is_again():
    assert signal_to_rating(Signal.AVOIDED) is Rating.Again
