"""The LearningSpace roster (multidata.learningspace.crawl_roster and
scripts/ls_roster.py), with no network. All people here are invented."""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from multidata import learningspace as ls

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=ls.LOCAL_TZ)

ACT = {"ID": "act1", "Title": " CSE 9Z Station (2026) ",
       "StartDate": "2026-02-01T05:00:00.000Z", "EndDate": "2026-03-01T04:55:00.000Z"}
OLD_ACT = {"ID": "act0", "Title": "Old", "StartDate": "2023-01-01T05:00:00.000Z",
           "EndDate": "2024-06-30T04:55:00.000Z"}
EV1 = {"ID": 77, "ActivityID": "act1", "StartTime": "2026-02-26 12:45:00", "EndTime": "2026-02-26 12:45:00"}
EV2 = {"ID": 78, "ActivityID": "act1", "StartTime": "2026-02-27 12:45:00", "EndTime": "2026-02-27 16:00:00"}


def frow(rec, fid, length, ev=77, when="2026-02-26T21:22:04.000Z"):
    return {"VideoRecordingID": rec, "VideoFileID": fid, "LengthSeconds": length,
            "RoomID": "room-1", "EventID": ev, "RecordingDateTime": when,
            "Filename": f"{fid}.mp4", "CameraID": 1}


def resp(case, learner, part="Faculty Observation and Narrative", submitter="obs-1",
         date="2026-02-26T21:42:00.000Z"):
    return {"CaseID": case, "LearnerID": learner, "CaseTitle": "Case One ",
            "CasePart": part, "SubmitterID": submitter, "SubmitterName": "Dr Hidden",
            "LearnerName": "Pat Hidden", "SubmitDate": date}


class FakeClient:
    """Like the real API, `recordings` ignores the event it is asked about and
    stamps every row with it."""

    def __init__(self, events=None, files=None, responses=None, pair_recs=None, vfiles=None):
        self.events_ = events if events is not None else [EV1, EV2]
        self.files = files or {77: [frow("rec1", "f1", 1200), frow("rec1", "f2", 1210),
                                    frow("rec1", "f2", 1210)],      # duplicate row
                               78: [frow("rec2", "f3", None, ev=78)]}
        self.responses = responses or [resp("c1", "l1"), resp("c1", "l1", part="Self Evaluation",
                                                              submitter="l1"),
                                       resp("c2", "l2")]
        self.pair_recs = pair_recs or {("c1", "l1"): [precs("rec1")],
                                       ("c2", "l2"): [precs("rec2")]}
        self.vfiles = vfiles or {}
        self.lookups = []
        self.calls = 0

    def activities(self): return [ACT, OLD_ACT]
    def events(self, a): return self.events_
    def event_files(self, e): return self.files.get(e, [])
    def activity_responses(self, a): return self.responses
    def video_files(self, g): return self.vfiles.get(g, [])

    def recordings(self, e, c, l):
        self.lookups.append((e, c, l))
        return [{**r, "EventID": e} for r in self.pair_recs.get((c, l), [])]


def precs(guid, when="2026-02-26T21:22:04.000Z", activity="act1"):
    return {"ID": guid, "RoomID": "room-9", "ActivityID": activity, "RecordingDateTime": when}


class TestFilters:
    def test_activities_since_keeps_open_ended_and_later(self):
        open_ended = {"ID": "a2", "Title": "x", "EndDate": None}
        assert ls.activities_since([ACT, OLD_ACT, open_ended]) == [ACT, open_ended]

    def test_events_since_drops_earlier_and_future(self):
        early = {**EV1, "StartTime": "2024-06-30 23:00:00"}
        future = {**EV1, "StartTime": "2026-12-01 09:00:00"}
        on_day = {**EV1, "StartTime": "2024-07-01 08:00:00"}
        assert ls.events_since([early, future, on_day, EV1], now=NOW) == [on_day, EV1]

    def test_match_activity(self):
        assert ls.match_activity(ACT, "act1")
        assert ls.match_activity(ACT, "cse 9z")
        assert not ls.match_activity(ACT, "CSE 2B")


class TestFields:
    def test_duration_is_longest_file_and_files_deduplicated(self):
        files = ls.files_by_recording(FakeClient().files[77])["rec1"]
        f = ls.roster_fields("rec1", files, ACT, EV1, rooms={"room-1": "11"})
        assert f["duration_s"] == 1210.0 and f["n_files"] == 2
        assert f["ls_event_id"] == "77" and f["activity_title"] == "CSE 9Z Station (2026)"
        assert f["recording_start_utc"] == "2026-02-26T21:22:04Z"
        assert f["room_number"] == "11"

    def test_no_length_leaves_duration_out(self):
        f = ls.roster_fields("rec2", [frow("rec2", "f3", None)], ACT, EV2)
        assert "duration_s" not in f and "room_number" not in f

    def test_pairs_take_only_faculty_observers(self):
        pairs = ls.learner_pairs(FakeClient().responses)
        assert pairs[("c1", "l1")]["observers"] == {"obs-1"}   # not the learner's self-eval
        assert pairs[("c1", "l1")]["case_title"] == "Case One"


class TestCrawl:
    def test_links_case_learner_preceptor_and_never_names(self):
        c = FakeClient()
        r = ls.crawl_roster(c, [ACT], now=NOW)
        assert set(r.rows) == {"rec1", "rec2"}
        assert r.rows["rec1"]["ls_preceptor_id"] == "obs-1"
        assert r.rows["rec2"]["ls_case_id"] == "c2" and r.rows["rec2"]["case_title"] == "Case One"
        for row in r.rows.values():
            assert not set(row) & ls.ROSTER_NAME_FIELDS
            assert "Hidden" not in repr(row)

    def test_one_lookup_per_pair_and_event_from_file_lists(self):
        # the pair endpoint answers for every event, stamped with the one asked
        c = FakeClient()
        r = ls.crawl_roster(c, [ACT], now=NOW)
        assert len(c.lookups) == 2 and {e for e, _, _ in c.lookups} == {77}
        assert r.rows["rec2"]["ls_event_id"] == "78"

    def test_two_observers_leave_preceptor_empty(self):
        c = FakeClient(responses=[resp("c1", "l1"), resp("c1", "l1", submitter="obs-2")])
        r = ls.crawl_roster(c, [ACT], now=NOW)
        assert "ls_preceptor_id" not in r.rows["rec1"] and r.warnings
        assert r.counts["unlinked"] == 1   # rec2: no pair for it

    def test_recordings_outside_events_are_added_from_pairs(self):
        c = FakeClient(pair_recs={("c1", "l1"): [precs("rec1"), precs("rec9", "2026-03-05T15:00:00.000Z")],
                                  ("c2", "l2"): [precs("rec8", "2024-06-01T15:00:00.000Z"),   # too early
                                                 precs("rec7", activity="other")]},          # not ours
                       vfiles={"rec9": [{"LengthSeconds": 900}, {"LengthSeconds": 905}]})
        r = ls.crawl_roster(c, [ACT], now=NOW)
        assert set(r.rows) == {"rec1", "rec2", "rec9"}
        nine = r.rows["rec9"]
        assert nine["duration_s"] == 905.0 and nine["n_files"] == 2 and "ls_event_id" not in nine
        assert nine["ls_room_id"] == "room-9" and nine["ls_preceptor_id"] == "obs-1"
        assert r.counts["recordings outside events"] == 1

    def test_limit_links_only_what_was_collected(self):
        c = FakeClient(pair_recs={("c1", "l1"): [precs("rec1"), precs("rec9")]})
        r = ls.crawl_roster(c, [ACT], limit=1, now=NOW)
        assert list(r.rows) == ["rec1"] and r.rows["rec1"]["ls_case_id"] == "c1"
        assert len(c.lookups) == 1   # stops once rec1 is tied

    def test_no_link(self):
        c = FakeClient()
        r = ls.crawl_roster(c, [ACT], link=False, now=NOW)
        assert set(r.rows) == {"rec1", "rec2"} and c.lookups == []


def load_script():
    spec = importlib.util.spec_from_file_location("ls_roster", ROOT / "scripts" / "ls_roster.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestScript:
    @pytest.fixture
    def run(self, monkeypatch, tmp_path):
        mod = load_script()
        client = FakeClient()
        monkeypatch.setattr(ls.Client, "from_env", classmethod(lambda cls, **kw: client))
        calls = []
        monkeypatch.setattr(mod.manifest, "all_ls_rooms", lambda path: {})
        monkeypatch.setattr(mod.manifest, "all_recordings", lambda path, roster=False: [])
        monkeypatch.setattr(mod.manifest, "upsert_ls_recording",
                            lambda fields, path: calls.append(fields) or ("added", fields["ls_recording_id"], []))
        return mod, calls, str(tmp_path / "m.sqlite")

    def test_dry_run_writes_nothing(self, run, capsys):
        mod, calls, db = run
        assert mod.main(["--manifest", db]) == 0
        out = capsys.readouterr().out
        assert calls == [] and "added 2" in out and "distinct preceptors 1" in out
        assert "Hidden" not in out

    def test_apply_upserts_guid_rows_without_names(self, run):
        mod, calls, db = run
        mod.main(["--manifest", db, "--apply"])
        assert {f["ls_recording_id"] for f in calls} == {"rec1", "rec2"}
        for f in calls:
            assert f["ls_synced_at"].endswith("Z")
            assert "recording_id" not in f and not set(f) & ls.ROSTER_NAME_FIELDS

    def test_preview_fills_empty_and_reports_conflicts(self):
        mod = load_script()
        assert mod.preview(None, {"ls_recording_id": "g"}) == ("added", [])
        st, conf = mod.preview({"duration_s": None, "room_number": "11"},
                               {"duration_s": 60.0, "room_number": "12"})
        assert st == "filled" and conf == [("room_number", "11", "12")]
        assert mod.same_title("activity_title", " A  B ", "a b")
