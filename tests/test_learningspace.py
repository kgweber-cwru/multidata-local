"""multidata.learningspace and scripts/ls_backfill.py, with no network.

All people here are invented. The shapes follow real Data API replies
(docs/learningspace_api.yaml), including their quirks: a case's title sits
in `Patient`, an event's EndTime can equal its StartTime, and one learner can
have two recordings starting in the same second.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from multidata import learningspace as ls
from multidata import manifest
from multidata.models import Recording

ROOT = Path(__file__).resolve().parents[1]

ACT = {"ID": "act1", "Title": "CSE 9Z Station (2026)", "StartDate": "2026-02-01T00:00:00.000Z"}
SCEN = {"ID": "scen1", "Patient": "CSE 9Z Case 1 Cough (2026)", "Title": None}
LEARNER = {"UserID": "stu1", "UserName": "Pat Example", "UserEmail": "x@example.edu"}
EVENT = {"ID": 77, "ActivityID": "act1", "Title": "Exam Day",
         "StartTime": "2026-02-26 12:45:00", "EndTime": "2026-02-26 12:45:00"}
REC = {"ID": "rec1", "RoomID": "room-guid-1", "ActivityID": "act1", "EventID": 77,
       "CaseID": "scen1", "RecordingDateTime": "2026-02-26T21:22:04.000Z", "StudentID": "stu1"}
FILES = [
    {"ID": "f16", "VideoRecordingID": "rec1", "LengthSeconds": 1208, "SizeBytes": 80,
     "LearningSpaceSerial": "LS-1", "Filename": "20260226-162204-v309407-16.mp4",
     "DownloadURL": "https://ls.example/dl/a.mp4"},
    {"ID": "f31", "VideoRecordingID": "rec1", "LengthSeconds": 1208, "SizeBytes": 83,
     "LearningSpaceSerial": "LS-2", "Filename": "20260226-162204-v309408-31.mp4",
     "DownloadURL": "https://ls.example/dl/b.mp4"},
]


def response(part="Faculty Observation and Narrative", submitter="Dr Observer",
             achieved=1, possible=1, submit="s1"):
    return {"CasePart": part, "SectionTitle": "Checklist", "SubmitID": submit,
            "SubmitterID": "sub-" + submitter, "SubmitterName": submitter,
            "SubmitDate": "2026-02-26T21:42:00.000Z",
            "AchievedPoints": achieved, "MaxAchievable": possible}


RESPONSES = [response(), response(achieved=0.5), response(possible=None, achieved=None),
             response(part="Pre-Encounter Learner", submitter="Pat Example", submit="s2")]


class FakeClient:
    """Stands in for ls.Client: canned rows per endpoint."""

    def __init__(self, **over):
        self.data = {"activities": [ACT], "cases": [SCEN], "events": [EVENT],
                     "students": [LEARNER], "recordings": [REC],
                     "files": {"rec1": FILES}, "responses": RESPONSES}
        self.data.update(over)
        self.calls = 0

    def activities(self): return self.data["activities"]
    def cases(self): return self.data["cases"]
    def events(self, activity_id): return self.data["events"]
    def students_named(self, name): return self.data["students"]
    def recordings(self, event_id, recording_id, student_id): return self.data["recordings"]
    def video_files(self, rid): return self.data["files"].get(rid, [])
    def responses(self, a, c, l): return self.data["responses"]


def recording_row(**over):
    row = {"recording_id": "900001", "activity_title": " CSE 9Z Station (2026) ",
           "case_title": "CSE 9Z Case 1 Cough (2026)", "room_number": "11",
           "learner_name": "Pat Example", "preceptor_name": "",
           "recording_start_time": "2026-02-26T16:22:00"}
    row.update(over)
    return row


def video_rows():
    return [{"camera": "16", "file_number": "v309407", "video_date": "2026-02-26",
             "video_time": "16:22:04", "filepath": "", "duration_s": 1208.6},
            {"camera": "31", "file_number": "v309408", "video_date": "2026-02-26",
             "video_time": "16:22:04", "filepath": "data/raw/900001/20260226-162204-v309408-31.mp4",
             "duration_s": 1208.5}]


# -- transport ---------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, gets):
        self.gets = list(gets)
        self.posts = []
        self.seen = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return FakeResponse(body={"accessToken": f"tok{len(self.posts)}"})

    def get(self, url, params=None, headers=None, timeout=None):
        self.seen.append((url, dict(params or {}), headers["Authorization"]))
        return self.gets.pop(0)


def page(rows, total=None, offset=0):
    return FakeResponse(body={"success": True, "total": len(rows) if total is None else total,
                              "offset": offset, "limit": len(rows), "data": rows})


def client(gets, tmp_path=None, **kw):
    sleeps = []
    t = iter(range(0, 10_000, 1))
    c = ls.Client("KEY", host="https://ls.example/", session=FakeSession(gets),
                  cache_dir=tmp_path, sleep=sleeps.append, clock=lambda: next(t) * 0.1, **kw)
    return c, sleeps


class TestClient:
    def test_token_exchange_then_bearer(self):
        c, _ = client([page([{"a": 1}])])
        c.get("/api/data/v1/cases")
        assert c.session.posts == [("https://ls.example/w/api/v2/auth.cgi",
                                    {"command": "token-for-apikey", "apiKeySecret": "KEY"})]
        assert c.session.seen[0][2] == "Bearer tok1"

    def test_requests_are_paced(self):
        c, sleeps = client([page([]), page([])], pace=1.0)
        c.get("/x")
        c.get("/y")
        assert sleeps and all(0 < s <= 1.0 for s in sleeps)

    def test_expired_token_refreshed_once(self):
        c, _ = client([FakeResponse(401, text="no"), page([{"a": 1}])])
        assert c.get("/x")["data"] == [{"a": 1}]
        assert len(c.session.posts) == 2
        assert c.session.seen[1][2] == "Bearer tok2"

    def test_second_401_is_an_error(self):
        c, _ = client([FakeResponse(401, text="no"), FakeResponse(401, text="no")])
        with pytest.raises(ls.LSError, match="401"):
            c.get("/x")

    @pytest.mark.parametrize("reply, msg", [
        (FakeResponse(404, text="<html>"), "404"),
        (FakeResponse(200, text="<html>"), "not JSON"),
        (FakeResponse(200, body={"success": False, "message": "Not found"}), "Not found"),
    ])
    def test_failures_raise(self, reply, msg):
        c, _ = client([reply])
        with pytest.raises(ls.LSError, match=msg):
            c.get("/x")

    def test_pagination(self):
        c, _ = client([page([1, 2], total=3), page([3], total=3, offset=2)])
        assert c.get_all("/x", Name="a") == [1, 2, 3]
        assert [s[1] for s in c.session.seen] == [
            {"limit": ls.PAGE, "offset": 0, "Name": "a"},
            {"limit": ls.PAGE, "offset": 2, "Name": "a"}]

    def test_cache_hit_makes_no_request(self, tmp_path):
        c, _ = client([page([1])], tmp_path)
        c.get("/x", a=1)
        calls = c.calls
        c2, _ = client([], tmp_path)
        assert c2.get("/x", a=1)["data"] == [1]
        assert c2.calls == 0 and calls == 2  # auth + get

    def test_refresh_bypasses_cache(self, tmp_path):
        client([page([1])], tmp_path)[0].get("/x")
        c, _ = client([page([2])], tmp_path, refresh=True)
        assert c.get("/x")["data"] == [2]

    def test_failed_reply_not_cached(self, tmp_path):
        c, _ = client([FakeResponse(500, text="err")], tmp_path)
        with pytest.raises(ls.LSError):
            c.get("/x")
        assert not list(tmp_path.iterdir())

    def test_from_env_needs_key(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LS_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ls.LSError, match="LS_API_KEY"):
            ls.Client.from_env()


# -- helpers ---------------------------------------------------------------------

class TestHelpers:
    def test_find_one_ignores_case_and_spacing(self):
        rows = [{"t": "CSE 2B  Long (2025) "}, {"t": "other"}]
        assert ls.find_one(rows, "cse 2b long (2025)", lambda r: r["t"], "x") is rows[0]

    @pytest.mark.parametrize("rows", [[], [{"t": "a"}, {"t": "A"}]])
    def test_find_one_none_or_several(self, rows):
        with pytest.raises(ls.LSError, match="matches"):
            ls.find_one(rows, "a", lambda r: r["t"], "x")

    def test_case_title_from_patient(self):
        assert ls.case_title(SCEN) == "CSE 9Z Case 1 Cough (2026)"

    @pytest.mark.parametrize("local, utc", [
        ("2026-02-26T16:22:00", "2026-02-26T21:22:00+00:00"),   # EST
        ("2025-04-21T10:22:00", "2025-04-21T14:22:00+00:00"),   # EDT
    ])
    def test_local_time_handles_dst(self, local, utc):
        from datetime import timezone
        assert ls.parse_local(local).astimezone(timezone.utc).isoformat() == utc

    def test_events_on_uses_local_date(self):
        evs = [EVENT, dict(EVENT, ID=78, StartTime="2026-02-25 12:45:00")]
        assert [e["ID"] for e in ls.events_on(evs, ls.parse_local("2026-02-26T16:22:00"))] == [77]

    def test_close_recordings_tolerance_and_order(self):
        start = ls.parse_local("2026-02-26T16:22:00")
        far = dict(REC, ID="far", RecordingDateTime="2026-02-26T21:30:00.000Z")
        near = dict(REC, ID="near", RecordingDateTime="2026-02-26T21:22:01.000Z")
        assert [r["ID"] for r in ls.close_recordings([far, REC, near], start)] == ["near", "rec1"]

    def test_choose_single(self):
        assert ls.choose_recording([REC], ["x"], lambda i: FILES) == (REC, FILES)

    def test_choose_breaks_tie_by_our_files(self):
        twin = dict(REC, ID="rec2")
        files = {"rec1": FILES, "rec2": [dict(f, Filename="other-" + f["Filename"]) for f in FILES]}
        rec, _ = ls.choose_recording([twin, REC], [FILES[0]["Filename"]], files.get)
        assert rec["ID"] == "rec1"

    def test_choose_refuses_when_files_dont_decide(self):
        twin = dict(REC, ID="rec2")
        with pytest.raises(ls.LSError, match="2 holding our files"):
            ls.choose_recording([twin, REC], [FILES[0]["Filename"]], lambda i: FILES)

    def test_choose_none(self):
        with pytest.raises(ls.LSError):
            ls.choose_recording([], [], lambda i: [])

    def test_scores_skip_text_boxes_and_split_parts(self):
        s = {(g["case_part"], g["submit_id"]): g for g in ls.summarize_scores(RESPONSES)}
        fon = s[("Faculty Observation and Narrative", "s1")]
        assert (fon["achieved"], fon["possible"], fon["items"]) == (1.5, 2.0, 2)
        assert ("Pre-Encounter Learner", "s2") in s

    def test_observers(self):
        assert ls.observers(ls.summarize_scores(RESPONSES)) == ["Dr Observer"]


# -- resolve -------------------------------------------------------------------

class TestResolve:
    def test_full_link(self):
        r = ls.resolve(recording_row(), video_rows(), FakeClient())
        assert r.ok and r.linked and not r.problems and not r.warnings
        assert r.recording_fields == {
            "ls_activity_id": "act1", "ls_case_id": "scen1", "ls_learner_id": "stu1",
            "ls_recording_id": "rec1", "ls_event_id": "77", "ls_room_id": "room-guid-1",
            "recording_start_utc": "2026-02-26T21:22:04Z", "preceptor_name": "Dr Observer"}
        assert r.video_fields["16"] == {"ls_file_id": "f16", "ls_server": "LS-1", "size_bytes": 80,
                                        "download_url": "https://ls.example/dl/a.mp4"}
        assert set(r.video_fields) == {"16", "31"}
        assert r.room == ("room-guid-1", "11")

    def test_only_observer_scores_kept(self):
        r = ls.resolve(recording_row(), video_rows(), FakeClient())
        assert {s["case_part"] for s in r.scores} == {"Faculty Observation and Narrative"}

    def test_extra_ls_file_reported(self):
        r = ls.resolve(recording_row(), video_rows()[:1], FakeClient())
        assert r.ok and r.extra_files == [FILES[1]["Filename"]]

    def test_our_file_missing_from_recording_blocks(self):
        vids = video_rows()
        vids[0]["file_number"] = "v999999"
        r = ls.resolve(recording_row(), vids, FakeClient())
        assert not r.ok and "camera 16" in r.problems[0]

    def test_no_event_gives_partial_link(self):
        r = ls.resolve(recording_row(), video_rows(), FakeClient(events=[]))
        assert r.ok and not r.linked
        assert set(r.recording_fields) == {"ls_activity_id", "ls_case_id", "ls_learner_id",
                                      "preceptor_name"}
        assert not r.video_fields and "scheduled event" in r.warnings[0]

    def test_twin_recordings_resolved_by_filename(self):
        twin = dict(REC, ID="rec0")
        other = [dict(f, Filename="x" + f["Filename"]) for f in FILES]
        r = ls.resolve(recording_row(), video_rows(),
                       FakeClient(recordings=[twin, REC], files={"rec0": other, "rec1": FILES}))
        assert r.linked and r.recording_fields["ls_recording_id"] == "rec1"

    @pytest.mark.parametrize("over, msg", [
        ({"students": []}, "learner: 0"),
        ({"students": [LEARNER, dict(LEARNER, UserID="stu2")]}, "learner: 2"),
        ({"students": [dict(LEARNER, UserName="Pat Examples")]}, "learner: 0"),  # substring hit
        ({"activities": []}, "activity: 0"),
        ({"cases": [SCEN, dict(SCEN, ID="scen2")]}, "case: 2"),
    ])
    def test_unmatched_identity_blocks(self, over, msg):
        r = ls.resolve(recording_row(), video_rows(), FakeClient(**over))
        assert not r.ok and len(r.problems) == 1 and r.problems[0].startswith(msg)

    def test_length_mismatch_is_a_warning(self):
        vids = video_rows()
        vids[0]["duration_s"] = 1100
        r = ls.resolve(recording_row(), vids, FakeClient())
        assert r.ok and any("length" in w for w in r.warnings)

    def test_two_observers_leave_preceptor_unset(self):
        resp = [response(), response(submitter="Dr Second", submit="s9")]
        r = ls.resolve(recording_row(), video_rows(), FakeClient(responses=resp))
        assert "preceptor_name" not in r.recording_fields and "2 different observers" in r.warnings[0]

    def test_no_recording_near_start(self):
        late = dict(REC, RecordingDateTime="2026-02-26T23:00:00.000Z")
        r = ls.resolve(recording_row(), video_rows(), FakeClient(recordings=[late]))
        assert r.ok and not r.linked


# -- planning ------------------------------------------------------------------

class TestPlan:
    def test_fills_only_empty(self):
        res = ls.resolve(recording_row(), video_rows(), FakeClient())
        plan = ls.plan_updates(recording_row(ls_room_id="room-guid-1"), video_rows(), res)
        assert "ls_room_id" not in plan.recording_set and plan.conflicts == []
        assert plan.recording_set["preceptor_name"] == "Dr Observer"

    def test_conflict_kept_and_reported(self):
        res = ls.resolve(recording_row(), video_rows(), FakeClient())
        plan = ls.plan_updates(recording_row(preceptor_name="Dr Someone"), video_rows(), res)
        assert "preceptor_name" not in plan.recording_set
        assert plan.conflicts == [("recording", "preceptor_name", "Dr Someone", "Dr Observer")]

    def test_video_conflict(self):
        res = ls.resolve(recording_row(), video_rows(), FakeClient())
        vids = video_rows()
        vids[0]["ls_file_id"] = "old"
        plan = ls.plan_updates(recording_row(), vids, res)
        assert ("camera 16", "ls_file_id", "old", "f16") in plan.conflicts
        assert "ls_file_id" not in plan.video_set["16"]

    def test_failed_resolution_plans_nothing(self):
        res = ls.resolve(recording_row(), video_rows(), FakeClient(students=[]))
        assert ls.plan_updates(recording_row(), video_rows(), res).empty

    def test_describe_masks_names(self):
        assert ls.describe("preceptor_name", "Dr Observer") == "(name)"
        assert ls.describe("ls_room_id", "g") == "g"


# -- manifest ------------------------------------------------------------------

def make_db(tmp_path):
    db = tmp_path / "m.sqlite"
    manifest.add_recording(Recording(recording_id="900001", activity_title=ACT["Title"], case_title=SCEN["Patient"],
                           room_number="11", learner_name="Pat Example",
                           recording_start_time="2026-02-26T16:22:00"), db)
    manifest.add_recording(Recording(recording_id="900002"), db)   # no video: prunable
    manifest.add_recording(Recording(recording_id="900003"), db)   # no video, but has gold
    manifest.record_gold("900003", path=db)
    for v in video_rows():
        manifest.add_video("900001", v["camera"], db, file_number=v["file_number"],
                           video_date=v["video_date"], video_time=v["video_time"],
                           filepath=v["filepath"], duration_s=v["duration_s"])
    return db


class TestManifest:
    def test_migrates_an_old_database(self, tmp_path):
        db = tmp_path / "old.sqlite"
        con = sqlite3.connect(db)
        con.executescript("""CREATE TABLE cases (case_id TEXT PRIMARY KEY);
            CREATE TABLE videos (case_id TEXT, camera TEXT, PRIMARY KEY (case_id, camera));""")
        con.close()
        manifest.connect(db).close()
        con = sqlite3.connect(db)
        ccols = {r[1] for r in con.execute("PRAGMA table_info(recordings)")}
        vcols = {r[1] for r in con.execute("PRAGMA table_info(videos)")}
        assert {"ls_recording_id", "recording_start_utc", "ls_synced_at"} <= ccols
        assert {"ls_file_id", "size_bytes", "download_url"} <= vcols
        assert {"recording_scores", "ls_rooms"} <= {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def test_renames_old_terms_keeping_values(self, tmp_path):
        """A manifest from before the rename: every table and column under its
        old name, with data. After connect, same values under new names."""
        db = tmp_path / "old.sqlite"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE cases (case_id TEXT PRIMARY KEY, event_name TEXT, case_name TEXT,
                group_name TEXT, ls_recording_guid TEXT, ls_case_guid TEXT);
            CREATE TABLE videos (case_id TEXT REFERENCES cases(case_id), camera TEXT,
                recording_id TEXT, PRIMARY KEY (case_id, camera));
            CREATE TABLE video_case_matches (filename TEXT PRIMARY KEY, status TEXT,
                case_id TEXT, camera TEXT, video_date TEXT, video_time TEXT, matched_at TEXT);
            CREATE TABLE case_scores (case_id TEXT, case_part TEXT, section TEXT, submit_id TEXT);
            CREATE TABLE gold (id INTEGER PRIMARY KEY, case_id TEXT REFERENCES cases(case_id));
            CREATE TABLE stage_runs (id INTEGER PRIMARY KEY, stage TEXT, case_id TEXT,
                camera TEXT, status TEXT, started_at TEXT, wall_s REAL);
            CREATE TABLE camera_views (camera TEXT, view_id TEXT, first_case_id TEXT,
                created_at TEXT, PRIMARY KEY (camera, view_id));
            CREATE TABLE video_views (case_id TEXT, camera TEXT, view_id TEXT, seconds REAL,
                stretches INTEGER);
            CREATE TABLE speaker_prosody (case_id TEXT, camera TEXT, speaker TEXT);
            INSERT INTO cases VALUES ('261505', 'Act', 'Scenario', 'Group', 'g-rec', 'g-case');
            INSERT INTO videos VALUES ('261505', '16', 'v309407');
            INSERT INTO video_case_matches VALUES ('f.mp4', 'matched', '261505', '16', 'd', 't', 'm');
            INSERT INTO case_scores VALUES ('261505', 'Faculty Observation', 'S', 's1');
            INSERT INTO gold VALUES (1, '261505');
            INSERT INTO stage_runs VALUES (1, 'asr', '261505', '16', 'done', 'x', 1.0);
            INSERT INTO camera_views VALUES ('16', 'v1', '261505', 'x');
            INSERT INTO video_views VALUES ('261505', '16', 'v1', 1.0, 1);
            INSERT INTO speaker_prosody VALUES ('261505', '16', 'SPEAKER_00');
        """)
        con.close()
        manifest.connect(db).close()
        manifest.connect(db).close()   # idempotent
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"cases", "video_case_matches", "case_scores"} & tables
        r = dict(con.execute("SELECT * FROM recordings").fetchone())
        assert (r["recording_id"], r["activity_title"], r["case_title"], r["learner_group"],
                r["ls_recording_id"], r["ls_case_id"]) == \
            ("261505", "Act", "Scenario", "Group", "g-rec", "g-case")
        v = dict(con.execute("SELECT * FROM videos").fetchone())
        assert (v["recording_id"], v["file_number"]) == ("261505", "v309407")
        for table in ("file_matches", "recording_scores", "gold", "stage_runs",
                      "video_views", "speaker_prosody"):
            assert con.execute(f"SELECT recording_id FROM {table}").fetchone()[0] == "261505"
        assert con.execute("SELECT first_recording_id FROM camera_views").fetchone()[0] == "261505"
        # foreign keys follow the renamed table
        assert "recordings" in con.execute(
            "SELECT sql FROM sqlite_master WHERE name='videos'").fetchone()[0]

    def test_update_recording(self, tmp_path):
        db = make_db(tmp_path)
        manifest.update_recording("900001", db, ls_recording_id="rec1")
        assert manifest.get_recording("900001", db).ls_recording_id == "rec1"

    def test_update_case_refuses_unknown_columns(self, tmp_path):
        with pytest.raises(ValueError, match="nope"):
            manifest.update_recording("900001", make_db(tmp_path), nope=1)

    def test_update_recording_unknown(self, tmp_path):
        with pytest.raises(ValueError, match="no recording"):
            manifest.update_recording("nope", make_db(tmp_path), ls_event_id="1")

    def test_scores_replaced_not_appended(self, tmp_path):
        db = make_db(tmp_path)
        rows = ls.summarize_scores(RESPONSES)
        manifest.set_recording_scores("900001", rows, db)
        manifest.set_recording_scores("900001", rows[:1], db)
        assert len(manifest.recording_scores("900001", db)) == 1

    def test_room_conflict_reported_and_kept(self, tmp_path):
        db = make_db(tmp_path)
        assert manifest.set_ls_room("g1", "11", db) is None
        assert manifest.set_ls_room("g1", "11", db) is None
        assert manifest.set_ls_room("g1", "12", db) == "11"
        assert manifest.all_ls_rooms(db) == {"g1": "11"}

    def test_prune_keeps_videos_and_references(self, tmp_path):
        db = make_db(tmp_path)
        assert manifest.prune_recordings_without_videos(db) == ["900002"]
        assert len(manifest.all_recordings(db)) == 3            # dry run
        assert manifest.prune_recordings_without_videos(db, dry_run=False) == ["900002"]
        assert {c.recording_id for c in manifest.all_recordings(db)} == {"900001", "900003"}


# -- the backfill script ---------------------------------------------------------

def load_script():
    spec = importlib.util.spec_from_file_location("ls_backfill", ROOT / "scripts" / "ls_backfill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBackfill:
    @pytest.fixture
    def run(self, tmp_path, monkeypatch, capsys):
        db = make_db(tmp_path)
        mod = load_script()
        monkeypatch.setattr(ls.Client, "from_env", classmethod(lambda cls, **kw: FakeClient()))

        def go(*args):
            code = mod.main(["--manifest", str(db), *args])
            return code, capsys.readouterr().out
        return db, go

    def test_dry_run_writes_nothing(self, run):
        db, go = run
        code, out = go("--prune")
        assert code == 0 and "1 linked" in out and "would drop 1" in out
        c = manifest.get_recording("900001", db)
        assert c.ls_recording_id == "" and c.preceptor_name == ""
        assert len(manifest.all_recordings(db)) == 3

    def test_apply_writes_everything(self, run):
        db, go = run
        code, out = go("--apply", "--prune")
        c = manifest.get_recording("900001", db)
        assert (c.ls_recording_id, c.preceptor_name, c.recording_start_utc) == \
            ("rec1", "Dr Observer", "2026-02-26T21:22:04Z")
        assert c.ls_synced_at
        assert manifest.get_video("900001", "16", db).ls_file_id == "f16"
        assert len(manifest.recording_scores("900001", db)) == 1
        assert manifest.all_ls_rooms(db) == {"room-guid-1": "11"}
        assert "dropped 1" in out

    def test_apply_twice_is_stable(self, run):
        db, go = run
        go("--apply")
        code, out = go("--apply")
        assert "!" not in out  # no conflicts with our own earlier write
        assert len(manifest.recording_scores("900001", db)) == 1

    def test_output_never_shows_names(self, run):
        _, go = run
        _, out = go("--apply")
        assert "Pat Example" not in out and "Dr Observer" not in out
        assert "preceptor_name (name)" in out

    def test_unknown_case_refused(self, run):
        _, go = run
        with pytest.raises(SystemExit, match="not in the manifest"):
            go("--recording", "123")
