"""Harvesting plays against a real database, where the hazard is real too.

The unit tests hand the command pre-labelled rows, so they cannot see the thing
that matters: Invidious rewrites `videos.updated` from `get_video`, and BOTH the
metadata route and `insert_video_into_playlist` reach it. So every candidate the
fill adds to a lane rewrites the same row a viewer's playback does. Reading the
metadata cache to tell them apart would call each of those a play, mark ~60
videos a night watched, and the next fill would retire them with a 365 day
cooldown -- the lanes eating themselves.

This runs `init`, then a real night, then the harvest, and asks the only
question that settles it: did the account's watch history change.
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
        instance.enrol(ALICE)
        engine.cmd_init(Args())
        HARNESS["before"] = instance.watched_by(ALICE.email)
        instance.night()
        HARNESS["after_a_night"] = instance.watched_by(ALICE.email)
        instance.harvest()
        HARNESS["after_harvesting_the_night"] = instance.watched_by(ALICE.email)
    except BaseException:
        instance.stop()
        raise


def tearDownModule():
    HARNESS["instance"].stop()


class AFillIsNotWatching(unittest.TestCase):
    def test_a_night_of_filling_adds_nothing_to_the_watch_history(self):
        self.assertEqual(HARNESS["before"], HARNESS["after_a_night"])

    def test_harvesting_after_that_night_adds_nothing_either(self):
        self.assertEqual(HARNESS["after_a_night"],
                         HARNESS["after_harvesting_the_night"])

    def test_the_night_really_did_refresh_cache_rows(self):
        """Or the test above passes because there was nothing to get wrong."""
        refreshed = HARNESS["instance"].db.value("SELECT count(*) FROM videos;")
        self.assertGreater(int(refreshed or 0), 0)

    def test_every_one_of_them_was_logged_as_this_bot_touching_it(self):
        instance = HARNESS["instance"]
        untouched = instance.db.value(
            "SELECT count(*) FROM videos v LEFT JOIN suggest.bot_touches b "
            "ON b.vid = v.id WHERE b.at IS NULL;")
        self.assertEqual(0, int(untouched or 0))


class AViewersOpenIsWatching(unittest.TestCase):
    """The other half: a refresh the bot did not cause must become a watch."""

    def setUp(self):
        self.instance = HARNESS["instance"]
        self.opened = self.instance.db.value("SELECT id FROM videos LIMIT 1;")

    def test_it_is_marked_watched(self):
        self.instance.somebody_opens(self.opened)
        self.instance.harvest()
        self.assertIn(self.opened, self.instance.watched_by(ALICE.email))

    def test_it_is_the_newest_entry_in_the_history(self):
        self.instance.somebody_opens(self.opened)
        self.instance.harvest()
        self.assertEqual(self.opened,
                         self.instance.watched_by(ALICE.email)[-1])

    def test_a_second_harvest_does_not_judge_it_twice(self):
        self.instance.somebody_opens(self.opened)
        self.instance.harvest()
        judged = self.instance.db.value("SELECT count(*) FROM suggest.plays;")
        self.instance.harvest()
        self.assertEqual(judged,
                         self.instance.db.value(
                             "SELECT count(*) FROM suggest.plays;"))

    def test_it_is_logged_with_the_watched_outcome(self):
        self.instance.somebody_opens(self.opened)
        self.instance.harvest()
        self.assertEqual("watched", self.instance.db.value(
            "SELECT outcome FROM suggest.plays WHERE vid = %s "
            "ORDER BY played DESC LIMIT 1;"
            % HARNESS["engine"].lit(self.opened)))


if __name__ == "__main__":
    unittest.main()
