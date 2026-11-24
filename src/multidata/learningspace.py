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
BIG_PAGE = 1000  # the server's maximum page
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

    def get_all(self, path, page=PAGE, **filters):
        """Every row, following offset/limit pagination (the server caps a
        page at 1000)."""
        rows, offset = [], 0
        while True:
            body = self.get(path, limit=page, offset=offset, **filters)
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

    # The roster (scripts/ls_roster.py) reads whole events and activities.
    def event_files(self, event_id):
        """Every file of every recording in one event: VideoRecordingID,
        LengthSeconds, RoomID, RecordingDateTime, Filename, ... but no case or
        learner. Named for NBOME, it serves any event."""
        return self.get_all(f"/api/data/v1/student-video-recordings/nbome/event/{event_id}",
                            page=BIG_PAGE)

    def activity_responses(self, activity_id):
        """Every checklist response in an activity (thousands of rows)."""
        return self.get_all(f"/api/data/v1/assessment/activity/{activity_id}/responses",
                            page=BIG_PAGE)


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
        # This endpoint ignores the event: it returns the pair's recordings
        # from every event, each stamped with the EventID asked for. So ask
        # once per event, keep one copy of each recording, and take its event
        # from the event file lists (below), not from the stamp.
        recs = {}
        for ev in events:
            for r in client.recordings(ev["ID"], scen["ID"], learner["UserID"]):
                recs.setdefault(r["ID"], r)
        rec, files = choose_recording(close_recordings(list(recs.values()), start),
                                      list(ours), client.video_files)
    except LSError as e:
        res.warnings.append(f"not linked to a recording: {e}")
        rec = None

    if rec is not None:
        res.linked = True
        rec_utc = parse_utc(rec["RecordingDateTime"])
        # Even with one event that day, the recording may be in none: made
        # outside the event's hours (260656, 2026-02-12).
        homes = [ev["ID"] for ev in events
                 if any(f.get("VideoRecordingID") == rec["ID"]
                        for f in client.event_files(ev["ID"]))]
        if len(homes) != 1:
            res.warnings.append(f"event: the recording is in {len(homes)} of the "
                                f"{len(events)} events that day; not set")
        res.recording_fields.update({
            "ls_recording_id": rec["ID"],
            "ls_event_id": str(homes[0]) if len(homes) == 1 else "",
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


# -- the roster: every recording since a date(scripts/ls_roster.py) ----------
#
# Two passes per activity. (1) Each in-scope event's file list, one paged call
# per event, gives every recording in it with its room, start and length, but
# not its case or learner. (2) The activity's checklist responses give its
# (case, learner) pairs and each pair's faculty observer; one lookup per pair
# returns that pair's recordings, tying pass-1 recordings to a case, a learner
# and a preceptor, and turning up recordings no event lists (made outside a
# scheduled event).
#
# The pair endpoint ignores the event in its path: it returns the pair's
# recordings from every event and stamps each with the EventID asked for.
# So one lookup per pair is enough, and a recording's event comes only from
# the event file lists.
#
# Roster rows carry GUIDs only: no names of people (docs/learningspace.md).

ROSTER_SINCE = "2024-07-01"
ROSTER_NAME_FIELDS = NAME_FIELDS | {"learner_group"}


def activities_since(activities, since=ROSTER_SINCE):
    """Activities that may hold an event on or after `since` (YYYY-MM-DD):
    ending on or after it, or with no end date."""
    return [a for a in activities if not a.get("EndDate") or a["EndDate"][:10] >= since]


def events_since(events, since=ROSTER_SINCE, now=None):
    """Events starting (local date) on or after `since` and not in the future."""
    now = now or datetime.now(LOCAL_TZ)
    return [e for e in events
            if e["StartTime"][:10] >= since and parse_local(e["StartTime"]) <= now]


def match_activity(activity, wanted):
    """--activity: the GUID exactly, or a substring of the title (any case)."""
    return activity["ID"] == wanted or _norm(wanted) in _norm(activity.get("Title"))


def files_by_recording(rows):
    """Event file rows -> {recording GUID: [file rows]}, one row per file."""
    out = {}
    for r in rows:
        files = out.setdefault(r["VideoRecordingID"], [])
        key = r.get("VideoFileID") or r.get("Filename")
        if not any((f.get("VideoFileID") or f.get("Filename")) == key for f in files):
            files.append(r)
    return out


def roster_fields(rec_guid, files, activity, event=None, recording=None, rooms=None):
    """recordings columns for one recording. `files` are its event file rows,
    or its video_files rows with `recording` (a pair-endpoint row) supplying
    the room and start. `duration_s` is its longest file (cameras run side by
    side), left out when LearningSpace has no length for any of them."""
    first = recording or (files[0] if files else {})
    fields = {"ls_recording_id": rec_guid,
              "ls_activity_id": activity["ID"],
              "activity_title": (activity.get("Title") or "").strip(),
              "ls_room_id": first.get("RoomID") or "",
              "n_files": len(files)}
    if event is not None:
        fields["ls_event_id"] = str(event["ID"])
    if first.get("RecordingDateTime"):
        fields["recording_start_utc"] = (parse_utc(first["RecordingDateTime"])
                                         .isoformat().replace("+00:00", "Z"))
    lengths = [f["LengthSeconds"] for f in files if f.get("LengthSeconds") is not None]
    if lengths:
        fields["duration_s"] = float(max(lengths))
    if rooms and fields["ls_room_id"] in rooms:
        fields["room_number"] = rooms[fields["ls_room_id"]]
    return fields


def learner_pairs(responses):
    """An activity's responses -> {(case GUID, learner GUID): {"case_title",
    "observers" (faculty-observation SubmitterIDs)}}."""
    pairs = {}
    for r in responses:
        if not r.get("CaseID") or not r.get("LearnerID"):
            continue
        p = pairs.setdefault((r["CaseID"], r["LearnerID"]),
                             {"case_title": (r.get("CaseTitle") or "").strip(),
                              "observers": set()})
        if OBSERVER_PART in (r.get("CasePart") or "").casefold() and r.get("SubmitterID"):
            p["observers"].add(r["SubmitterID"])
    return pairs


@dataclass
class Roster:
    rows: dict = field(default_factory=dict)       # recording GUID -> recordings columns
    counts: dict = field(default_factory=dict)     # what was seen, for the summary
    warnings: list = field(default_factory=list)   # no names in these

    def bump(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n


def crawl_roster(client, activities, since=ROSTER_SINCE, limit=None, link=True,
                 rooms=None, now=None, log=lambda s: None):
    """Every recording in `activities` since `since` (see above). `limit`
    stops collecting after that many recordings; linking then only ties what
    was collected and adds nothing. Returns a Roster."""
    out = Roster()
    full = lambda: limit is not None and len(out.rows) >= limit
    for act in activities:
        if full():
            break
        events = events_since(client.events(act["ID"]), since, now)
        out.bump("activities")
        if not events:
            continue
        out.bump("activities with events")
        mine = set()
        for ev in sorted(events, key=lambda e: e["StartTime"]):
            if full():
                break
            out.bump("events")
            for guid, files in files_by_recording(client.event_files(ev["ID"])).items():
                if full():
                    break
                if guid in out.rows:
                    out.warnings.append(f"recording {guid[:8]} listed in two events")
                    continue
                out.rows[guid] = roster_fields(guid, files, act, ev, rooms=rooms)
                mine.add(guid)
        log(f"  activity {act['ID'][:8]}: {len(events)} events, {len(mine)} recordings")
        if link and mine:
            _link(client, act, events[0]["ID"], mine, since, out, rooms,
                  add=limit is None)
    return out


def _link(client, act, any_event, mine, since, out, rooms, add):
    """Pass 2 for one activity: `mine` are its pass-1 GUIDs; `add` lets
    recordings no event lists join the roster."""
    pairs = learner_pairs(client.activity_responses(act["ID"]))
    out.bump("pairs", len(pairs))
    left = set(mine)
    for (case_id, learner_id), p in pairs.items():
        if not add and not left:
            break   # a --limit run: everything collected is tied
        recs = client.recordings(any_event, case_id, learner_id)
        out.bump("pair lookups")
        obs = sorted(p["observers"])
        if recs and len(obs) > 1:
            out.warnings.append(f"case {case_id[:8]}: {len(obs)} observers "
                                "for one learner, preceptor not set")
        for r in recs:
            row = out.rows.get(r["ID"])
            if row is None:
                if not add or (r.get("ActivityID") or act["ID"]) != act["ID"] \
                        or parse_utc(r["RecordingDateTime"]).astimezone(LOCAL_TZ) \
                           .date().isoformat() < since:
                    continue
                row = out.rows[r["ID"]] = roster_fields(
                    r["ID"], client.video_files(r["ID"]), act, recording=r, rooms=rooms)
                out.bump("recordings outside events")
            row.update({"ls_case_id": case_id, "ls_learner_id": learner_id})
            if p["case_title"]:
                row["case_title"] = p["case_title"]
            if len(obs) == 1:
                row["ls_preceptor_id"] = obs[0]
            left.discard(r["ID"])
    if left:
        out.bump("unlinked", len(left))
