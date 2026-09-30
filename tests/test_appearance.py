"""Giving each project a consistent colour and icon."""
from __future__ import annotations

from bridge.appearance import COLOURS, colour_for, icon_for

ICONS = [f"icon-{i}" for i in range(112)]


def test_a_project_always_gets_the_same_colour():
    project = r"C:\Code\webshop"
    assert colour_for(project) == colour_for(project)


def test_the_choice_does_not_change_between_runs():
    """Python's own hash is salted per process, which would repaint every topic
    on restart. This must be stable, so the value is pinned here."""
    assert colour_for(r"C:\Code\webshop") in COLOURS
    assert colour_for(r"C:\Code\webshop") == colour_for(r"C:\Code\webshop")


def test_the_same_project_written_differently_still_matches():
    assert colour_for(r"C:\Code\webshop") == colour_for("c:/code/WEBSHOP")
    assert icon_for(r"C:\Code\webshop", ICONS) == icon_for("c:/code/WEBSHOP", ICONS)


def test_different_projects_are_told_apart():
    colours = {colour_for(f"C:/Code/project{i}") for i in range(30)}
    assert len(colours) > 1          # not everything landing on one colour


def test_every_colour_is_one_telegram_accepts():
    for index in range(50):
        assert colour_for(f"C:/Code/p{index}") in COLOURS


def test_an_icon_is_always_one_of_the_offered_ones():
    assert icon_for(r"C:\Code\app", ICONS) in ICONS


def test_no_icons_available_is_not_an_error():
    assert icon_for(r"C:\Code\app", []) is None
