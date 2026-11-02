#!/usr/bin/env python
"""Build one annotation kit: a self-contained assignment for one annotator.

    python annotation/make_kit.py --case 261473 --annotator jamie          # blind (route A)
    python annotation/make_kit.py --case 261473 --annotator jamie --seed   # draft-seeded (route B):
        # first run lists the draft's speakers; then add --roles SPEAKER_00=PATIENT,...

Writes annotation/kits/<case>/<annotator>/ containing everything the annotator
needs and nothing else:

    <case>.pass1.eaf   the template's six tiers, both media files ALREADY LINKED:
                       empty (blind), or filled with the seed transcript's
                       utterances on the role tiers (seeded; see below)
    media/<case>.mp4   proxy video (480p) -- or the original with --full-video
    media/<case>.wav   the audio_camera wav, copied as-is
    kit.json           what this kit is, for the record
    README.txt         two paragraphs for the annotator

Runs on the private side (needs the manifest and the raw media).

Seeded kits (docs/transcription_standards.md §9, route B; multidata.seeding):
the seed is always `seeding.SEED_ENGINE`/`SEED_MODEL`, made on first use
(GPU, a few minutes) and never changed afterwards. Each diarized speaker's
utterances go on the role tier given by `--roles`; speakers left out go on
an extra UNASSIGNED tier that `submit_checks.py` requires to be empty at
hand-in. The seed's provenance (engine, model, SHA-256 of the transcript,
role map) is written to kit.json and to the .eaf header, and
`scripts/eaf_to_gold.py` carries it into the gold record, so seeded gold can
always be told apart from blind gold.

STATUS (2026-09-23): the cloud remote-desktop delivery mechanism this was
built for has been torn out -- see docs/status.md, "Why the cloud annotation
desktop was abandoned". This module is kept because the problem it
solves (ELAN opens with no "locate media" dialog, no matter which machine the
annotator ends up working on) is independent of *how* the kit gets to that
machine. Nothing here assumes a VM specifically. It does assume some machine
mounts every case at the same fixed path -- see CANONICAL_ROOT below -- which
was true of the VM design and will need a real decision again once the next
delivery mechanism (most likely a dedicated laptop) is chosen.

The one thing to understand before changing this file: the .eaf records its
media twice, in MEDIA_URL (absolute) and RELATIVE_MEDIA_URL. Both are written
against CANONICAL_ROOT, the path every annotator machine is assumed to mount
the case at. That is what stops ELAN showing a "locate media" dialog on a
machine that isn't the one the file was made on. Change CANONICAL_ROOT and you
must change where kits actually get staged, or every kit breaks.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from multidata import manifest, seeding  # noqa: E402
from multidata.tools import which  # noqa: E402

TEMPLATE = ROOT / "elan" / "template.etf"
KITS_DIR = ROOT / "annotation" / "kits"

# Where an annotator's machine is assumed to mount a case. Baked into every
# kit's .eaf, so it is the same string on every machine, forever -- but this
# was chosen for the abandoned VM design and hasn't been revisited since. See
# the module docstring before assuming it's still the right path.
CANONICAL_ROOT = "/srv/multidata/case"

# 480p H.264. Video's job is answering "who is speaking" -- it does not need to
# be broadcast quality, and a smaller file decodes faster over whatever remote
# connection ends up carrying it.
PROXY_FFMPEG = [
    "-vf", "scale=-2:480", "-c:v", "libx264", "-preset", "veryfast",
    "-crf", "28", "-an",
]


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def resolve(path_str):
    """Manifest paths are a mix of repo-relative and absolute. Accept both."""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


def find_media(case_id, camera, manifest_path):
    """The .wav and .mp4 for one camera of one case."""
    video = manifest.get_video(case_id, camera, path=manifest_path)
    if video is None:
        raise SystemExit(f"no videos row for case {case_id} camera {camera}")

    wav = resolve(video.audio_path) if video.audio_path else None
    if wav is None or not wav.exists():
        raise SystemExit(
            f"no audio for {case_id}/{camera} at {wav} -- run the audio stage first")

    mp4 = resolve(video.filepath) if video.filepath else None
    if mp4 is None or not mp4.exists():
        raise SystemExit(f"no video for {case_id}/{camera} at {mp4}")

    return wav, mp4


def seeded_annotations(utts, roles, span=None):
    """{tier: [(start_ms, end_ms, text)]} from seed utterances and a role map.
    Utterances of speakers without a role go to UNASSIGNED. Overlapping
    utterances that land on one tier (two speakers mapped to one role) are
    merged, since ELAN can't hold overlaps on a tier."""
    tiers = {}
    for spk, a, b, text in utts:
        if span and (b <= span[0] or a >= span[1]):
            continue
        tier = roles.get(spk, seeding.UNASSIGNED_TIER)
        tiers.setdefault(tier, []).append([int(round(a * 1000)), int(round(b * 1000)), text])
    for tier, anns in tiers.items():
        anns.sort()
        merged = []
        for a, b, text in anns:
            if merged and a < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
                merged[-1][2] += " " + text
            else:
                merged.append([a, b, text])
        tiers[tier] = [tuple(m) for m in merged if m[1] > m[0]]
    return tiers


def write_eaf(out_path, case_id, span=None, seeded=None, seed_prov=None):
    """The template's six tiers, with both media files pre-linked.

    `seeded` ({tier: [(start_ms, end_ms, text)]}, from `seeded_annotations`)
    pre-fills the tiers for a seeded pass 1; `seed_prov` goes in the header.

    `span` (start, end) in seconds marks an excerpt window as one NOTES
    annotation, so the constraint sits on the timeline rather than in a README.
    NOTES is excluded from both gold.txt and gold.rttm (see elan.py), so this
    cannot reach a published reference.
    """
    import pympi

    eaf = pympi.Elan.Eaf(str(TEMPLATE))

    tiers = list(eaf.tiers.keys())
    expected = ["LEARNER", "PATIENT", "PRECEPTOR",
                "ANNOUNCEMENT", "OUTSIDE_ROOM", "NOTES"]
    if tiers != expected:
        raise SystemExit(
            f"{TEMPLATE} has tiers {tiers}, expected {expected} -- "
            "changing the tier set is a standards change "
            "(transcription_standards.md §4/§12), not a file edit")

    base = f"{CANONICAL_ROOT}/{case_id}/media"
    eaf.add_linked_file(f"file://{base}/{case_id}.mp4",
                        relpath=f"media/{case_id}.mp4", mimetype="video/mp4")
    eaf.add_linked_file(f"file://{base}/{case_id}.wav",
                        relpath=f"media/{case_id}.wav", mimetype="audio/x-wav")

    if span:
        start_ms, end_ms = int(span[0] * 1000), int(span[1] * 1000)
        eaf.add_annotation("NOTES", start_ms, end_ms, "ANNOTATE ONLY THIS WINDOW")

    if seeded:
        if seeded.get(seeding.UNASSIGNED_TIER):
            eaf.add_tier(seeding.UNASSIGNED_TIER)
        for tier, anns in seeded.items():
            for a, b, text in anns:
                eaf.add_annotation(tier, a, b, text)
    if seed_prov:
        for key in ("pass1_source", "seed_engine", "seed_model", "seed_sha256"):
            eaf.add_property(f"multidata:{key}", str(seed_prov[key]))

    eaf.to_file(str(out_path))


def make_proxy(src_mp4, dest_mp4):
    subprocess.run([which("ffmpeg"), "-y", "-loglevel", "error", "-i", str(src_mp4)]
                   + PROXY_FFMPEG + [str(dest_mp4)], check=True)


README_SEEDED = """Case {case_id} -- SEEDED pass 1 (correct a machine draft)

Open {case_id}.pass1.eaf: the speaker tiers already hold a machine
transcript, cut into utterances and assigned to roles, with both media files
loaded.
{window}
Follow the annotator guide's section "Correcting a seeded draft" (on the
desktop under "Guide & cheat sheet"). Listen to everything: fix boundaries,
words, speakers and markup to the standards. Do not trust the draft --
treat each line as a guess to check against the audio.
{unassigned}
When you are done, send Kate a note that you are done.
After that, pass 1 is frozen -- report any later concern rather
than reopening it.
"""

README = """Case {case_id} -- blind pass 1

Open {case_id}.pass1.eaf with six empty tiers and both media files already loaded.
You do not need to create a file, pick a template, or locate any media.
{window}
Follow the annotator guide, on the desktop under "Guide & cheat sheet".
Work blind: no machine transcript, no other annotator's file. If someone
offers you one, decline.

When you are done, send Kate a note that you are done.
After that, pass 1 is frozen -- report any later concern rather
than reopening it.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", required=True)
    ap.add_argument("--annotator", required=True)
    ap.add_argument("--camera", default=None,
                    help="default: the case's audio_camera")
    ap.add_argument("--manifest", default=str(ROOT / "manifest.sqlite"))
    ap.add_argument("--out-dir", default=str(KITS_DIR))
    ap.add_argument("--full-video", action="store_true",
                    help="copy the original video instead of a 480p proxy")
    ap.add_argument("--span-start", type=float, default=None,
                    help="excerpt window, seconds -- marks it on the timeline")
    ap.add_argument("--span-end", type=float, default=None)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing kit")
    ap.add_argument("--seed", action="store_true",
                    help=f"draft-seeded pass 1 (standards §9 route B): pre-fill the tiers "
                         f"from the {seeding.SEED_ENGINE}/{seeding.SEED_MODEL} transcript")
    ap.add_argument("--roles", default=None, nargs="+",
                    help="with --seed: SPEAKER_00=PATIENT,SPEAKER_01=LEARNER,... -- commas, "
                         "spaces or both between pairs (run --seed without it first to see "
                         "the speakers)")
    args = ap.parse_args()
    if args.roles and not args.seed:
        raise SystemExit("--roles only goes with --seed")

    if (args.span_start is None) != (args.span_end is None):
        raise SystemExit("--span-start and --span-end go together")
    span = (args.span_start, args.span_end) if args.span_start is not None else None

    manifest_path = Path(args.manifest)
    case = manifest.get_case(args.case, path=manifest_path)
    if case is None:
        raise SystemExit(
            f"case {args.case} is not in the manifest -- a kit records which "
            "case it is, so it needs the row")

    camera = args.camera or case.audio_camera
    if not camera:
        raise SystemExit(
            f"case {args.case} has no audio_camera set; pass --camera")

    wav, mp4 = find_media(args.case, camera, manifest_path)

    seeded = seed_prov = None
    if args.seed:
        seed = seeding.seed_transcript_path(args.case, camera, ROOT / "data")
        if not seed.exists():
            print(f"making the seed transcript ({seeding.SEED_ENGINE}/{seeding.SEED_MODEL}, "
                  "GPU, a few minutes)")
            seeding.make_seed_transcript(
                wav, seed, rttm=ROOT / "data" / "diarization" / str(args.case) / f"{camera}.rttm")
        utts = seeding.utterances(json.loads(seed.read_text()))
        if not args.roles:
            print(f"\nSpeakers in the seed transcript of {args.case} "
                  "(contains real speech: keep on secure storage):\n")
            for spk, s in seeding.speaker_overview(utts).items():
                print(f"{spk}: {s['talk_s'] / 60:.1f} min in {s['utterances']} utterances, "
                      f"first at {s['first'] / 60:.1f} min")
                for ex in s["examples"]:
                    print("    " + ex)
            print("\nDecide each speaker's role, then run again with "
                  "--roles SPEAKER_00=PATIENT,SPEAKER_01=LEARNER,... "
                  f"(roles: {', '.join(seeding.ROLE_TIERS)}; leave a speaker out to "
                  "send their speech to UNASSIGNED for the annotator to sort).")
            return
        roles = seeding.parse_roles(",".join(args.roles))
        unknown = set(roles) - {u[0] for u in utts}
        if unknown:
            raise SystemExit(f"--roles names speakers not in the seed transcript: {sorted(unknown)}")
        seeded = seeded_annotations(utts, roles, span=span)
        seed_prov = seeding.provenance(seed, roles)

    kit = Path(args.out_dir) / args.case / args.annotator
    if kit.exists() and not args.force:
        raise SystemExit(f"{kit} already exists -- pass --force to replace it")
    (kit / "media").mkdir(parents=True, exist_ok=True)

    dest_wav = kit / "media" / f"{args.case}.wav"
    dest_mp4 = kit / "media" / f"{args.case}.mp4"

    print(f"copying audio  {wav.name}")
    shutil.copy2(wav, dest_wav)

    if args.full_video:
        print(f"copying video  {mp4.name}")
        shutil.copy2(mp4, dest_mp4)
    else:
        print(f"building proxy {mp4.name} -> 480p (this takes a minute)")
        make_proxy(mp4, dest_mp4)

    eaf_path = kit / f"{args.case}.pass1.eaf"
    write_eaf(eaf_path, args.case, span=span, seeded=seeded, seed_prov=seed_prov)

    (kit / "kit.json").write_text(json.dumps({
        "case_id": args.case,
        "annotator": args.annotator,
        "camera": camera,
        "canonical_root": f"{CANONICAL_ROOT}/{args.case}",
        "eaf": eaf_path.name,
        "span_start": args.span_start,
        "span_end": args.span_end,
        "proxy_video": not args.full_video,
        "source_video": str(mp4.relative_to(ROOT)) if mp4.is_relative_to(ROOT) else str(mp4),
        "source_video_sha256": sha256(mp4),
        "source_wav_sha256": sha256(dest_wav),
        "template_sha256": sha256(TEMPLATE),
        **(seed_prov or {"pass1_source": "blind"}),
    }, indent=2) + "\n")

    window = ""
    if span:
        window = (f"\nThis case is an EXCERPT. Annotate only "
                  f"{args.span_start:.0f}s to {args.span_end:.0f}s -- the window is "
                  "marked on the NOTES tier.\n")
    if seeded:
        n_un = len(seeded.get(seeding.UNASSIGNED_TIER, []))
        unassigned = (f"\n{n_un} utterances are on the UNASSIGNED tier (speakers with no role yet):\n"
                      "move each to the right speaker's tier or delete it -- the file\n"
                      "can't be handed in while UNASSIGNED has anything on it.\n") if n_un else ""
        (kit / "README.txt").write_text(README_SEEDED.format(
            case_id=args.case, window=window, unassigned=unassigned))
    else:
        (kit / "README.txt").write_text(
            README.format(case_id=args.case, window=window))

    size_mb = sum(f.stat().st_size for f in kit.rglob("*") if f.is_file()) / 1e6
    print(f"\nkit ready: {kit}  ({size_mb:.0f} MB)")
    print("  no delivery mechanism is wired up right now -- see "
          "docs/status.md")


if __name__ == "__main__":
    main()
