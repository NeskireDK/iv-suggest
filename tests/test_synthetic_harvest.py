"""Harvesting plays against a real database, where the hazard is real too.

The unit tests hand the command pre-labelled rows, so they cannot see the thing
that matters: Invidious rewrites `videos.updated` from `get_video`, and BOTH the
metadata route and `insert_video_into_playlist` reach it. So every candidate the
fill adds to a lane rewrites the same row a viewer's playback does. Calling one
of those a play marks it watched, and the next fill retires it under a 365 day
cooldown -- the lanes eating themselves.

It is not one write per video either. A genre lane fetches a video's metadata
and adds it to the playlist minutes later; the second call finds the cache row
too fresh to rewrite, so one `updated` stands against two touches at different
moments. That gap is what the separation has to survive, and it only appears
against a database that models the staleness rule.

Everything that writes happens once, in setUpModule, so every test below is a
read of a recorded state rather than a step in a sequence.
"""

import unittest

from support import load
from synthetic import ALICE, LANES_FILE, Args, Instance, require_docker

HARNESS = {}


def setUpModule():
    require_docker()
    engine = load(IV_SUGGEST_ACCOUNT=ALICE.email, IV_SUGGEST_CONFIG=LANES_FILE)
    instance = Instance(engine).start()
    HARNESS.update(engine=engine, instance=instance)
    try:
        drive_a_night_then_a_viewer(engine, instance)
    except BaseException:
        instance.stop()
        raise


def drive_a_night_then_a_viewer(engine, instance):
    instance.enrol(ALICE)
    engine.cmd_init(Args())
    HARNESS["before"] = instance.watched_by(ALICE.email)
    instance.night()
    HARNESS["after_a_night"] = instance.watched_by(ALICE.email)
    HARNESS["cache_rows"] = int(count(instance, "videos") or 0)
    HARNESS["touch_rows"] = int(count(instance, "suggest.bot_touches") or 0)
    instance.harvest()
    HARNESS["after_harvesting_the_night"] = instance.watched_by(ALICE.email)
    HARNESS["judged_the_night"] = int(count(instance, "suggest.plays") or 0)

    opened = instance.db.value("SELECT min(id) FROM videos;")
    HARNESS["opened"] = opened
    instance.somebody_opens(opened)
    instance.harvest()
    HARNESS["after_a_viewers_open"] = instance.watched_by(ALICE.email)
    HARNESS["judged_the_open"] = int(count(instance, "suggest.plays") or 0)
    HARNESS["outcome_of_the_open"] = instance.db.value(
        "SELECT outcome FROM suggest.plays WHERE vid = %s "
        "ORDER BY played DESC LIMIT 1;" % engine.lit(opened))
    instance.harvest()
    HARNESS["judged_after_a_repeat"] = int(count(instance, "suggest.plays") or 0)
    HARNESS["after_a_repeat"] = instance.watched_by(ALICE.email)


def count(instance, table):
    return instance.db.value("SELECT count(*) FROM %s;" % table)


def tearDownModule():
    HARNESS["instance"].stop()


class AFillIsNotWatching(unittest.TestCase):
    def test_a_night_of_filling_adds_nothing_to_the_watch_history(self):
        self.assertEqual(HARNESS["before"], HARNESS["after_a_night"])

    def test_harvesting_after_that_night_adds_nothing_either(self):
        self.assertEqual(HARNESS["after_a_night"],
                         HARNESS["after_harvesting_the_night"])

    def test_the_night_really_did_refresh_cache_rows(self):
        """Or the two tests above pass because there was nothing to get wrong."""
        self.assertGreater(HARNESS["cache_rows"], 0)

    def test_the_fill_wrote_more_touches_than_there_are_cache_rows(self):
        """The pair of calls per video is the case a single latest touch loses."""
        self.assertGreater(HARNESS["touch_rows"], HARNESS["cache_rows"])

    def test_nothing_from_the_night_was_judged_a_play(self):
        self.assertEqual(0, HARNESS["judged_the_night"])


class AViewersOpenIsWatching(unittest.TestCase):
    """The other half: a refresh the engine did not cause must become a watch."""

    def test_it_is_marked_watched(self):
        self.assertIn(HARNESS["opened"], HARNESS["after_a_viewers_open"])

    def test_it_becomes_the_newest_entry_in_the_history(self):
        self.assertEqual(HARNESS["opened"], HARNESS["after_a_viewers_open"][-1])

    def test_it_is_logged_with_the_watched_outcome(self):
        self.assertEqual("watched", HARNESS["outcome_of_the_open"])

    def test_the_engines_own_older_touches_did_not_veto_it(self):
        """The touch rows are aged, not deleted, so the window is really read."""
        self.assertEqual(1, HARNESS["judged_the_open"])


class ARepeatedHarvest(unittest.TestCase):
    def test_it_judges_nothing_twice(self):
        self.assertEqual(HARNESS["judged_the_open"],
                         HARNESS["judged_after_a_repeat"])

    def test_it_leaves_the_watch_history_alone(self):
        self.assertEqual(HARNESS["after_a_viewers_open"],
                         HARNESS["after_a_repeat"])


if __name__ == "__main__":
    unittest.main()
