# annotation/

Tools for getting a gold transcript out of a human, minus the part that gets
ELAN in front of them.

**The cloud remote-desktop delivery mechanism that used to live here is gone**
— it never produced usable audio, and after several dead ends it was torn out
rather than patched further. Full account in
[docs/current_status.md](../docs/current_status.md), under "Annotation
environment abandoned." Read that before picking this up again; it's a status
note, not a design doc, and it says plainly what to decide next and what not
to re-litigate.

**What's still here works and is still useful, independent of how ELAN
actually gets in front of an annotator:**

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a self-contained kit: a `.eaf` with the right six tiers and both media files pre-linked (no ELAN "locate media" dialog), a proxy video, the case wav, and a README. Tested against real data — see the module docstring for the one thing not to break if you touch it. |
| `submit_checks.py` | Two checks on a finished `.eaf`: refuse if any segment is empty, warn if annotations fall outside a declared excerpt window. Standard library only, so it can run anywhere. |
| `pull_submission.py` | Finished `.eaf` → `data/gold/<case>/` → `scripts/eaf_to_gold.py`. Takes the file from a local directory (`--from-dir`) or a GCS bucket (`--bucket`, a holdover that still works). |

Nothing here assumes a cloud VM specifically. `make_kit.py` does assume every
annotator's machine mounts a case at the same fixed path
(`CANONICAL_ROOT` — see its docstring) — that assumption is sound for *any*
single delivery mechanism, but the path itself was chosen for the abandoned
VM design and hasn't been reconsidered since.

`kits/` is gitignored — it holds real-name media.

## Using what's left, by hand

```bash
conda activate md-speech

python annotation/make_kit.py --case 261473 --annotator jamie
# -> annotation/kits/261473/jamie/ -- get this to the annotator any way you like

# ... they annotate, however they're doing that right now ...

python annotation/pull_submission.py --case 261473 --annotator jamie \
    --from-dir <wherever the finished kit came back to>
```
