"""Channels no lane may offer, from a playlist on the account plus the config.

The playlist half is what makes the blocklist usable from any client: adding a
video to "Blocked" is a channel block, so nothing here needs a new endpoint or a
fork change. Both halves read the config through the same one-parse cache, which
is what the last class here is about.
"""

import os
import tempfile
import unittest

from support import load

ME = "andre@example.com"


class Blocklist(unittest.TestCase):

    def loaded(self, text="", rows=()):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        fh.write("lanes:\n  - id: suggested\n    title: Suggested\n" + text)
        fh.close()
        self.path = fh.name
        self.addCleanup(lambda: os.path.exists(fh.name) and os.unlink(fh.name))
        mod = load(IV_SUGGEST_CONFIG=fh.name, IV_SUGGEST_ACCOUNT=ME)
        self.asked = []
        mod.query = lambda sql: self.asked.append(sql) or [list(r) for r in rows]
        return mod

    def test_a_video_in_the_blocklist_playlist_blocks_its_whole_channel(self):
        mod = self.loaded(rows=[("UC-1", "Bad Channel")])
        self.assertEqual({"UC-1": "Bad Channel"}, mod.read_blocked())

    def test_the_default_playlist_title_is_used_when_none_is_configured(self):
        mod = self.loaded(rows=[("UC-1", "Bad Channel")])
        mod.read_blocked()
        self.assertIn("'blocked'", self.asked[0].lower())

    def test_an_empty_playlist_title_disables_the_playlist_source(self):
        mod = self.loaded('blocklist: {playlist: ""}\n', rows=[("UC-1", "Bad")])
        self.assertEqual({}, mod.read_blocked())
        self.assertEqual([], self.asked, "a disabled source must cost no query")

    def test_a_channel_named_in_the_config_needs_no_video_to_tap(self):
        mod = self.loaded("blocklist: {channels: [UC-9]}\n")
        self.assertEqual({"UC-9": "UC-9"}, mod.read_blocked())

    def test_the_two_sources_are_unioned(self):
        mod = self.loaded("blocklist: {channels: [UC-9]}\n",
                          rows=[("UC-1", "Bad Channel")])
        self.assertEqual({"UC-1": "Bad Channel", "UC-9": "UC-9"},
                         mod.read_blocked())

    def test_a_channel_with_no_name_falls_back_to_its_id(self):
        mod = self.loaded(rows=[("UC-1", "")])
        self.assertEqual({"UC-1": "UC-1"}, mod.read_blocked())

    def test_an_entry_carrying_no_channel_id_is_ignored(self):
        mod = self.loaded(rows=[("", "orphan")])
        self.assertEqual({}, mod.read_blocked())

    def test_a_lane_playlist_can_never_feed_itself_back_as_a_blocklist(self):
        """Naming a lane "Blocked" would otherwise empty that lane every run."""
        mod = self.loaded(rows=[("UC-1", "Bad")])
        mod.read_blocked()
        self.assertIn("suggest.lanes", self.asked[0])

    def test_the_blocklist_reads_the_cached_config_not_the_file_again(self):
        """Once per account per scrape was once per account too many."""
        mod = self.loaded("blocklist: {channels: [UC-9]}\n")
        mod.load_config()
        os.unlink(self.path)
        self.assertEqual({"UC-9": "UC-9"}, mod.read_blocked())


class OneParse(unittest.TestCase):
    """lanes.yml is read from disk once, however many accounts a run serves."""

    def config(self, text):
        fh = tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False)
        fh.write(text)
        fh.close()
        self.addCleanup(lambda: os.path.exists(fh.name) and os.unlink(fh.name))
        return fh.name

    def test_the_lane_list_survives_the_file_going_away_after_the_first_read(self):
        path = self.config("lanes:\n  - id: suggested\n    title: Suggested\n")
        mod = load(IV_SUGGEST_CONFIG=path, IV_SUGGEST_ACCOUNT=ME)
        first = [lane["id"] for lane in mod.load_config()]
        os.unlink(path)
        self.assertEqual(first, [lane["id"] for lane in mod.load_config()])
        self.assertEqual(["suggested"], first)

    def test_the_users_block_survives_it_too(self):
        path = self.config("lanes: []\nusers:\n  - email: %s\n" % ME)
        mod = load(IV_SUGGEST_CONFIG=path, IV_SUGGEST_ACCOUNT=ME)
        mod.load_users()
        os.unlink(path)
        self.assertEqual([ME], [u["email"] for u in mod.load_users()])


if __name__ == "__main__":
    unittest.main()
