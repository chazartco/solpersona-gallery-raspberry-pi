"""docs/solpersona/test-vectors.json — every port of the clock must reproduce these exactly (CONFORMANCE A)."""

from solpersona_gallery.clock import ServerClock, offset_sample
from solpersona_gallery.rules import image_choice, position_at, schedule_open, to_minutes


def test_position_at(vectors):
    for case in vectors["positionAt"]:
        got = position_at(case["timeline"], case["clock"], case["nowMs"])
        assert got == case["expect"], case["name"]


def test_clock_offset(vectors):
    for case in vectors["clockOffset"]:
        assert offset_sample(case["serverTime"], case["sentAt"], case["receivedAt"]) == case["expect"], case["name"]
        clock = ServerClock(mono=lambda: 0.0, wall=lambda: 0.0)
        clock.add(case["serverTime"], case["sentAt"], case["receivedAt"])
        assert clock.offset == case["expect"], case["name"]


def test_image_choice(vectors):
    for case in vectors["imageChoice"]["cases"]:
        got = image_choice(case["quality"], case["screenWidth"], case["canHoldOriginal"], case["original"], case["display"])
        assert got == case["expect"], case


def test_schedule(vectors):
    for case in vectors["schedule"]["cases"]:
        assert schedule_open(case["schedule"], to_minutes(case["time"])) is case["expect"], case
