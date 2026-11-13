"""LearningSpace Data API client and the lookups that tie a recording in our
manifest to LearningSpace's own records (docs/learningspace.md).

    from multidata import learningspace as ls
    client = ls.Client.from_env()           # LS_API_KEY from .env
    result = ls.resolve(recording, videos, client)

The API is read-only reporting ("views"): every endpoint is a GET returning
`{success, total, offset, limit, data: [...]}`. Columns can be filtered by
name (`?UserName=...`, substring match). Authentication is a JWT from
`/w/api/v2/auth.cgi`, exchanged for the API key.

How one of our recordings is found (every step is one GET, cached):

  activity  by title        <- our recordings.activity_title
  case      by title        <- our recordings.case_title (LS puts the title in `Patient`)
  learner   by name         <- our recordings.learner_name
  event     by local date   <- our recordings.recording_start_time
  recording for (event, case, learner), nearest the start minute
  files     for the recording -> filenames, matched to our videos rows

The recording's GUID is LearningSpace's identity for it; the six-digit
`recording_id` we use comes only from the front end's export.

Be gentle with the server: requests are paced (`pace` seconds apart) and
every response is cached on disk, so a dry run followed by `--apply` costs
the server nothing the second time. Cached responses hold real names: the
cache lives under data/, which is gitignored.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from multidata.paths import DATA_DIR

HOST = "https://simlearningspace.case.edu"
LOCAL_TZ = ZoneInfo("America/New_York")   # the sim center's wall clock
CACHE_DIR = DATA_DIR / "ls_api" / "cache"
PAGE = 200
START_TOLERANCE_S = 120  # our start time is truncated to the minute


class LSError(Exception):
    pass


class Client:
    """Paced, cached GETs against the Data API."""

    def __init__(self, api_key, host=HOST, pace=1.0, cache_dir=CACHE_DIR,
                 session=None, refresh=False, sleep=time.sleep, clock=time.monotonic):
        import requests  # md-utility / md-speech; kept out of module import
        self.api_key = api_key
        self.host = host.rstrip("/")
        self.pace = pace
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.session = session or requests.Session()
        self.refresh = refresh
        self._sleep, self._clock = sleep, clock
        self._token = None
        self._last = None
        self.calls = 0  # network requests actually made (cache hits excluded)

    @classmethod
    def from_env(cls, **kw):
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
        key = os.getenv("LS_API_KEY")
        if not key:
            raise LSError("LS_API_KEY is not set (put it in .env)")
        return cls(key, **kw)

    # -- transport ------------------------------------------------------
    def _wait(self):
        if self._last is not None:
            gap = self.pace - (self._clock() - self._last)
            if gap > 0:
                self._sleep(gap)
        self._last = self._clock()

    def _authenticate(self):
        self._wait()
        self.calls += 1
        r = self.session.post(f"{self.host}/w/api/v2/auth.cgi", timeout=60,
                              json={"command": "token-for-apikey", "apiKeySecret": self.api_key})
        r.raise_for_status()
        try:
            self._token = r.json()["accessToken"]
        except (ValueError, KeyError):
            raise LSError(f"no token in auth reply: {r.text[:100]!r}") from None

    def _cache_path(self, path, params):
        key = json.dumps([path, sorted(params.items())])
        return self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"

    def get(self, path, **params):
        """One page, as the parsed JSON object. Raises LSError on failure."""
        cp = self._cache_path(path, params) if self.cache_dir else None
        if cp and cp.exists() and not self.refresh:
            return json.loads(cp.read_text())["response"]
        for attempt in (1, 2):
            if self._token is None:
                self._authenticate()
            self._wait()
            self.calls += 1
            r = self.session.get(self.host + path, params=params, timeout=180,
                                 headers={"Authorization": f"Bearer {self._token}"})
            if r.status_code == 401 and attempt == 1:
                self._token = None  # expired: get a new one, once
                continue
            break
        if r.status_code != 200:
            raise LSError(f"GET {path} -> HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise LSError(f"GET {path} -> not JSON") from None
        if not body.get("success", False):
            raise LSError(f"GET {path} -> {body.get('message')!r}")
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps({"path": path, "params": params,
                                      "fetched_at": datetime.now(timezone.utc).isoformat(),
                                      "response": body}))
        return body

    def get_all(self, path, **filters):
        """Every row, following offset/limit pagination."""
        rows, offset = [], 0
        while True:
            body = self.get(path, limit=PAGE, offset=offset, **filters)
            data = body.get("data") or []
            rows += data
            offset += len(data)
            if not data or offset >= body.get("total", 0):
                return rows

    # -- endpoints --------------------------------------------------------
    def activities(self):
        return self.get_all("/api/data/v1/activities")

    def cases(self):
        return self.get_all("/api/data/v1/cases")

    def events(self, activity_id):
        return self.get_all(f"/api/data/v1/events/activity/{activity_id}")

    def students_named(self, name):
        return self.get_all("/api/data/v1/students", UserName=name)

    def recordings(self, event_id, case_id, student_id):
        return self.get_all(f"/api/data/v1/student-video-recordings/event/{event_id}"
                            f"/case/{case_id}/student/{student_id}")

    def video_files(self, recording_guid):
        return self.get_all(f"/api/data/v1/video-files/video-recording/{recording_guid}")

    def responses(self, activity_id, case_id, learner_id):
        return self.get_all(f"/api/data/v1/assessment/activity/{activity_id}"
                            f"/case/{case_id}/learner/{learner_id}/responses")


# -- pure helpers (no network; unit-tested directly) ---------------------

def _norm(s):
    return " ".join((s or "").split()).casefold()


def case_title(row):
    """LS case rows carry the title in `Patient` (and `Title` is empty)."""
    return row.get("Title") or row.get("Patient") or ""


def find_one(rows, wanted, key, what):
    """The single row whose `key(row)` equals `wanted`, ignoring case and
    spacing. Raises LSError for none or several."""
    hits = [r for r in rows if _norm(key(r)) == _norm(wanted)]
    if len(hits) != 1:
        raise LSError(f"{what}: {len(hits)} matches")
    return hits[0]


def parse_local(s):
    """'2026-02-26T16:22:00' or '2026-02-12 14:10:00' (sim-center local) -> aware."""
    return datetime.fromisoformat(s.replace(" ", "T")).replace(tzinfo=LOCAL_TZ)


def parse_utc(s):
    """'2026-02-26T21:22:04.000Z' -> aware UTC."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def events_on(events, start_local):
    """Events whose local date is the recording's date (an event's EndTime is
    not always later than its StartTime, so dates are what we trust)."""
    day = start_local.date()
    return [e for e in events if parse_local(e["StartTime"]).date() == day]


def close_recordings(recordings, start_local, tolerance_s=START_TOLERANCE_S):
    """Recordings starting within the tolerance of our (minute-truncated)
    start time, nearest first."""
    def gap(r):
        return abs((parse_utc(r["RecordingDateTime"]) - start_local).total_seconds())
    return sorted((r for r in recordings if gap(r) <= tolerance_s), key=gap)


def choose_recording(close, our_files, files_of):
    """One recording from `close` (see close_recordings). With several, the
    one whose files include all of ours wins: LearningSpace sometimes holds
    two recordings of one learner starting in the same second, each with its
    own files. Returns (recording, its files)."""
    if not close:
        raise LSError("recording: none near the start time")
    if len(close) == 1:
        return close[0], files_of(close[0]["ID"])
    hits = []
    for r in close:
        files = files_of(r["ID"])
        if set(our_files) <= {f["Filename"] for f in files}:
            hits.append((r, files))
    if len(hits) != 1:
        raise LSError(f"recording: {len(close)} near the start time, "
                      f"{len(hits)} holding our files")
    return hits[0]


OBSERVER_PART = "faculty observation"


def summarize_scores(responses):
    """Checklist responses -> one row per (part, section, submission):
    points achieved and possible, the submitter, and when."""
    groups = {}
    for r in responses:
        k = (r.get("CasePart") or "", r.get("SectionTitle") or "", r.get("SubmitID") or "")
        g = groups.setdefault(k, {"case_part": k[0], "section": k[1], "submit_id": k[2],
                                  "submitter_id": r.get("SubmitterID") or "",
                                  "submitter_name": r.get("SubmitterName") or "",
                                  "submitted_at": r.get("SubmitDate") or "",
                                  "achieved": 0.0, "possible": 0.0, "items": 0})
        if r.get("MaxAchievable") is not None:  # text boxes carry no points
            g["achieved"] += float(r.get("AchievedPoints") or 0)
            g["possible"] += float(r["MaxAchievable"])
            g["items"] += 1
    return [g for g in groups.values() if g["items"]]


def observers(scores):
    """Names of whoever submitted a faculty-observation part: the preceptor."""
    return sorted({s["submitter_name"] for s in scores
                   if OBSERVER_PART in s["case_part"].casefold() and s["submitter_name"]})


# -- resolving one recording ------------------------------------------------

@dataclass
class Resolution:
    recording_id: str
    ok: bool = False          # nothing blocking: recording_fields etc. can be written
    linked: bool = False      # the recording itself was found (GUID, files)
    problems: list = field(default_factory=list)       # blocking: nothing is written
    warnings: list = field(default_factory=list)       # reported; the links are still written
    recording_fields: dict = field(default_factory=dict)  # recordings columns to set
    video_fields: dict = field(default_factory=dict)   # camera -> videos columns
    scores: list = field(default_factory=list)
    room: tuple = ()                                    # (room GUID, our room number)
    extra_files: list = field(default_factory=list)     # in LS, not on our disk


def _video_filename(v):
    if v.get("filepath"):
        return Path(v["filepath"]).name
    return (f"{v['video_date'].replace('-', '')}-{v['video_time'].replace(':', '')}"
            f"-{v['file_number']}-{v['camera']}.mp4")


class Lookups:
    """Activity, case and event lists, fetched once per run."""

    def __init__(self, client):
        self.client = client
        self._activities = self._cases = None
        self._events = {}

    @property
    def activities(self):
        if self._activities is None:
            self._activities = self.client.activities()
        return self._activities

    @property
    def cases(self):
        if self._cases is None:
            self._cases = self.client.cases()
        return self._cases

    def events(self, activity_id):
        if activity_id not in self._events:
            self._events[activity_id] = self.client.events(activity_id)
        return self._events[activity_id]


def resolve(recording, videos, client, lookups=None):
    """Find one manifest recording in LearningSpace.

    `recording` is a recordings row (mapping); `videos` its videos rows. Returns a
    Resolution. Never guesses between candidates.

    The activity, case and learner must each match exactly, or nothing is
    written (`ok` False). The recording is reached through a scheduled event;
    a recording made outside any event can't be reached, and then the result
    is partly linked: `ok` True, `linked` False, with the activity, case,
    learner, scores and preceptor but no recording GUID or file links.
    """
    lk = lookups or Lookups(client)
    res = Resolution(recording_id=recording["recording_id"])
    try:
        act = find_one(lk.activities, recording["activity_title"], lambda a: a["Title"], "activity")
        scen = find_one(lk.cases, recording["case_title"], case_title, "case")
        people = [s for s in client.students_named(recording["learner_name"].strip())
                  if _norm(s["UserName"]) == _norm(recording["learner_name"])]
        if len(people) != 1:
            raise LSError(f"learner: {len(people)} exact name matches")
        learner = people[0]
        start = parse_local(recording["recording_start_time"])
    except (LSError, ValueError) as e:
        res.problems.append(str(e))
        return res
    res.ok = True
    res.recording_fields = {"ls_activity_id": act["ID"], "ls_case_id": scen["ID"],
                       "ls_learner_id": learner["UserID"]}

    ours = {_video_filename(v): v for v in videos}
    try:
        events = events_on(lk.events(act["ID"]), start)
        if not events:
            raise LSError("no scheduled event on the recording's date, so the "
                          "API can't reach the recording")
        recs = []
        for ev in events:
            recs += client.recordings(ev["ID"], scen["ID"], learner["UserID"])
        rec, files = choose_recording(close_recordings(recs, start), list(ours),
                                      client.video_files)
    except LSError as e:
        res.warnings.append(f"not linked to a recording: {e}")
        rec = None

    if rec is not None:
        res.linked = True
        rec_utc = parse_utc(rec["RecordingDateTime"])
        res.recording_fields.update({
            "ls_recording_id": rec["ID"],
            "ls_event_id": str(rec["EventID"]),
            "ls_room_id": rec.get("RoomID") or "",
            "recording_start_utc": rec_utc.isoformat().replace("+00:00", "Z"),
        })
        if rec.get("RoomID"):
            res.room = (rec["RoomID"], recording["room_number"])
        by_name = {f["Filename"]: f for f in files}
        for name, v in ours.items():
            f = by_name.get(name)
            if f is None:
                res.problems.append(f"camera {v['camera']}: our file is not among "
                                    "the recording's files")
                continue
            res.video_fields[v["camera"]] = {
                "ls_file_id": f.get("ID") or "",
                "ls_server": f.get("LearningSpaceSerial") or "",
                "size_bytes": f.get("SizeBytes"),
                "download_url": f.get("DownloadURL") or "",
            }
            if v.get("duration_s") and f.get("LengthSeconds") is not None \
                    and abs(v["duration_s"] - f["LengthSeconds"]) > 2:
                res.warnings.append(f"camera {v['camera']}: length {v['duration_s']:.0f}s "
                                    f"here, {f['LengthSeconds']}s in LearningSpace")
        res.extra_files = sorted(set(by_name) - set(ours))
        if res.problems:
            res.ok = res.linked = False
            return res

    try:
        # Only the faculty observer's checklist: the recording's grade. The
        # learner's own quizzes and self-evaluation are left in LearningSpace.
        res.scores = [g for g in summarize_scores(
                          client.responses(act["ID"], scen["ID"], learner["UserID"]))
                      if OBSERVER_PART in g["case_part"].casefold()]
    except LSError as e:
        res.warnings.append(f"scores: {e}")
    obs = observers(res.scores)
    if len(obs) == 1:
        res.recording_fields["preceptor_name"] = obs[0]
    elif len(obs) > 1:
        res.warnings.append(f"preceptor: {len(obs)} different observers submitted; not set")
    return res


# -- planning writes: fill empty fields, never overwrite ---------------------

NAME_FIELDS = {"preceptor_name", "learner_name", "sp_name"}


@dataclass
class Plan:
    recording_id: str
    recording_set: dict = field(default_factory=dict)       # column -> new value
    video_set: dict = field(default_factory=dict)      # camera -> {column: value}
    conflicts: list = field(default_factory=list)      # (where, column, ours, theirs)

    @property
    def empty(self):
        return not self.recording_set and not any(self.video_set.values())


def _merge(current, proposed, where, plan_conflicts):
    out = {}
    for col, new in proposed.items():
        if new in (None, ""):
            continue
        old = current.get(col)
        if old in (None, ""):
            out[col] = new
        elif str(old) != str(new):
            plan_conflicts.append((where, col, old, new))
    return out


def plan_updates(recording, videos, res):
    """What `res` would change in the manifest: only empty fields are filled;
    a different existing value is a conflict, reported and left alone."""
    plan = Plan(recording_id=recording["recording_id"])
    if not res.ok:
        return plan
    plan.recording_set = _merge(recording, res.recording_fields, "recording", plan.conflicts)
    by_cam = {v["camera"]: v for v in videos}
    for cam, proposed in res.video_fields.items():
        s = _merge(by_cam[cam], proposed, f"camera {cam}", plan.conflicts)
        if s:
            plan.video_set[cam] = s
    return plan


def describe(col, value):
    """Safe to print: names are never shown."""
    return "(name)" if col in NAME_FIELDS else value
