#!/usr/bin/env python
"""Build one annotation kit: a self-contained assignment for one annotator.

    python annotation/make_kit.py --case 261473 --annotator jamie

Writes annotation/kits/<case>/<annotator>/ containing everything the annotator
needs and nothing else:

    <case>.pass1.eaf   six empty tiers, both media files ALREADY LINKED
    media/<case>.mp4   proxy video (480p) -- or the original with --full-video
    media/<case>.wav   the audio_camera wav, copied as-is
    kit.json           what this kit is, for the record
    README.txt         two paragraphs for the annotator

Runs on the private side (needs the manifest and the raw media). Nothing here
touches GCP -- pushing the kit out is `push_kit.sh`, deliberately a separate
step, because the network boundary is crossed by hand (annotation design §2).

The one thing to understand before changing this file: the .eaf records its
media twice, in MEDIA_URL (absolute) and RELATIVE_MEDIA_URL. Both are written
against CANONICAL_ROOT, the path every annotator VM mounts the case at. That is
what stops ELAN showing a "locate media" dialog on a machine that isn't the one
the file was made on (annotation design §4.2). Change CANONICAL_ROOT and you
must change where the VM stages kits, or every kit breaks.
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

from multidata import manifest  # noqa: E402

TEMPLATE = ROOT / "elan" / "template.etf"
KITS_DIR = ROOT / "annotation" / "kits"

# Where an annotator VM mounts a case. Baked into every kit's .eaf, so it is
# the same string on every machine, forever. See the module docstring.
CANONICAL_ROOT = "/srv/multidata/case"

# 480p H.264. Video's job is answering "who is speaking" -- it does not need to
# be broadcast quality, and a smaller file decodes faster over a remote desktop
# (annotation design §4.3).
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


def write_eaf(out_path, case_id, span=None):
    """The template's six tiers, with both media files pre-linked.

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

    eaf.to_file(str(out_path))


def make_proxy(src_mp4, dest_mp4):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src_mp4)]
                   + PROXY_FFMPEG + [str(dest_mp4)], check=True)


README = """Case {case_id} -- blind pass 1

Open the desktop's "Start annotating" button. ELAN will open
{case_id}.pass1.eaf with six empty tiers and both media files already loaded.
You do not need to create a file, pick a template, or locate any media.
{window}
Follow the annotator guide, on the desktop under "Guide & cheat sheet".
Work blind: no machine transcript, no other annotator's file. If someone
offers you one, decline.

When you are done, click "Submit finished pass". It checks your file and
uploads it. After that, pass 1 is frozen -- report any later concern rather
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
    args = ap.parse_args()

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
    write_eaf(eaf_path, args.case, span=span)

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
    }, indent=2) + "\n")

    window = ""
    if span:
        window = (f"\nThis case is an EXCERPT. Annotate only "
                  f"{args.span_start:.0f}s to {args.span_end:.0f}s -- the window is "
                  "marked on the NOTES tier.\n")
    (kit / "README.txt").write_text(
        README.format(case_id=args.case, window=window))

    size_mb = sum(f.stat().st_size for f in kit.rglob("*") if f.is_file()) / 1e6
    print(f"\nkit ready: {kit}  ({size_mb:.0f} MB)")
    print(f"  push it with: annotation/gcp/push_kit.sh "
          f"--case {args.case} --annotator {args.annotator}")


if __name__ == "__main__":
    main()
