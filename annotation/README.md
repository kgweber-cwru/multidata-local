# annotation/

Everything for getting a gold transcript out of a human: build an assignment,
put it on a machine they can reach, get the finished file back, turn it into a
gold reference.

Design and reasoning live in
[../docs/annotation_environment_design.md](../docs/annotation_environment_design.md)
(the environment) and
[../docs/annotator_image_design.md](../docs/annotator_image_design.md) (the
machine). **Read those before changing anything here** — most of what looks
arbitrary is load-bearing, and the docs say which.

---

## The shape of it

```
              PRIVATE NETWORK                  |         GCP
                                               |
  manifest + media + template                  |
        |                                      |
        |  make_kit.py                         |
        v                                      |
   annotation/kits/<case>/<annotator>/         |
        |                                      |
        |  push_kit.sh  ----------------------> bucket ---> annotator's machine
        |                                      |                  |
        |                                      |            they annotate
        |                                      |                  |
   data/gold/<case>/  <--- pull_submission.py <--- bucket <--- Submit button
        |                                      |
        |  scripts/eaf_to_gold.py              |
        v                                      |
   benchmarks/references/  (tracked, de-identified)
```

The private network can reach out but **cannot be reached in**, and annotators
can't see it from home or campus. That's why the machines are in the cloud, and
why the boundary is crossed outward, by hand, by `push_kit.sh` and
`pull_submission.py`. Roughly 57 MB out per case and a few hundred KB back — a
dozen-odd times for the first campaign. Don't automate it; see the environment
design §2 for why.

---

## Running a campaign

**First**, check [config.sh](config.sh) — bucket, region, zone, image name, and
the ELAN download all live there, and every script reads it. Nothing needs
exporting. Anything in your environment still overrides it for one run:

```bash
ZONE=us-central1-a annotation/gcp/new_vm.sh --annotator jamie
```

**Once per project**, set up the network, bucket, and identity:

```bash
annotation/gcp/setup_project.sh
```

Safe to re-run — every step skips if already done. It creates the IAP firewall
rule (without which nothing can be reached at all), turns on Private Google
Access so machines with no public IP can still reach Cloud Storage, creates the
bucket with versioning, and creates the service account.

**Once**, build the machine image:

```bash
annotation/gcp/build_image.sh v1
```

The ELAN URL and checksum come from `config.sh`.

`build_image.sh` is safe to re-run: it deletes a leftover builder VM from a
failed attempt before starting, and it checks up front that `annotator-v1`
doesn't already exist rather than discovering that at the last step. A failed
build leaves the builder up on purpose so you can log in and look; the next run
clears it.

`build_image.sh` hands the ELAN settings to `install.sh` by naming them in the
remote command. Exporting them in your own shell is necessary but not sufficient:
`gcloud compute ssh` starts a fresh shell on the builder, and `sudo` resets the
environment again on top of that, so nothing travels on its own. If you run
`install.sh` by hand, put them on the command line —
`sudo ELAN_DEB_URL=... bash install.sh`.

**Once per annotator:**

```bash
annotation/gcp/new_vm.sh --annotator jamie
# grant them access (new_vm.sh prints the two commands), then send them
# annotation/gcp/connect.sh and walk them through it once -- 15 minutes
```

**Per case, three commands:**

```bash
conda activate md-speech

python annotation/make_kit.py --case 261473 --annotator jamie
annotation/gcp/push_kit.sh   --case 261473 --annotator jamie
# ... they annotate, then click Submit ...
python annotation/pull_submission.py --case 261473 --annotator jamie
```

**When the campaign ends:** pull every submission first, then
`annotation/gcp/delete_vm.sh --annotator jamie`. Deleting the machine deletes
its disk, which is the point — it removes the copy of real-name data that was
living on it.

### Excerpts

A case can be an excerpt rather than a whole encounter:

```bash
python annotation/make_kit.py --case 261473 --annotator jamie \
    --span-start 120 --span-end 420
```

The window gets marked on the `NOTES` tier so it sits on the timeline rather
than in a README nobody re-reads. `NOTES` is excluded from both `gold.txt` and
`gold.rttm`, so the marker can't reach a published reference.

---

## What each piece does

| | |
|---|---|
| `make_kit.py` | Manifest + template + media → a kit on local disk. The substance: it writes the pre-linked `.eaf`. |
| `submit_checks.py` | Two checks on an annotated `.eaf`. Runs on the machine *and* again locally on the way back in. Standard library only, so the VM needs no packages. |
| `pull_submission.py` | Submission → `data/gold/` → `scripts/eaf_to_gold.py`. |
| `config.sh` | Every project setting, in one place. Sourced by the shell scripts, parsed by the Python ones. Tracked — none of it is secret. |
| `gcp/setup_project.sh` | One-time: firewall, Private Google Access, bucket, service account. |
| `gcp/build_image.sh` | Build `annotator-vN` once. |
| `gcp/new_vm.sh` | One machine per annotator. |
| `gcp/connect.sh` | Handed to the annotator. Opens a private tunnel to their machine. |
| `gcp/push_kit.sh` | Kit → bucket → staged on their machine. |
| `gcp/delete_vm.sh` | Tear down after pulling submissions. |
| `image/install.sh` | Builds the desktop. Runs as root during image build only. |
| `image/desktop/` | The three launchers and the small helpers behind them. |
| `image/elan_prefs/` | The one ELAN setting that isn't a default, plus notes. |

`kits/` is gitignored — it holds real-name media.

---

## Network shape, since it bit twice

| Machine | Public IP | Why |
|---|---|---|
| The image builder | **yes**, ephemeral, for one build | It installs Debian packages and downloads ELAN, so it needs the open internet. A VM with no external address and no NAT has no outbound route at all. |
| Annotator machines | **no** | They install nothing — it's all in the image — and talk only to Cloud Storage, which Private Google Access reaches without an external address. |

So the machines that hold the recordings can reach Storage and nothing else,
while the only thing that ever touches the open internet is a VM that gets
deleted at the end of the build. That's a better split than giving everything
Cloud NAT, and it's free.

If your organisation forbids external IPs on VMs, the builder can't be created
and `setup_project.sh` prints the Cloud NAT alternative.

---

## If a build dies on port 22 or a dropped connection

Everything the build does to the VM goes through the IAP tunnel to a machine
that has just booted, and that path comes up raggedly — sshd, the guest agent
propagating keys, and the tunnel backend each become ready at their own pace.
A step can fail once and work fifteen seconds later.

`build_image.sh` handles this now: it waits for **three consecutive** successful
SSH connections before proceeding (one success right after boot proves nothing),
gives up with the real error after five minutes rather than hanging, and retries
the file copy. If you see it anyway, `--reuse` on the leftover builder skips the
whole boot race.

The install step is deliberately **not** retried: if `install.sh` itself failed,
re-running it is wrong, because it isn't idempotent once the `annotator` account
exists.

---

## Iterating on the image without waiting

Booting a VM and installing several hundred packages is most of a build's wall
clock. When you're fixing `install.sh` a line at a time, reuse the builder the
failed run left behind:

```bash
annotation/gcp/build_image.sh v2 --reuse
```

It skips the boot and the package install and just re-runs the install script.
Two limits, both enforced rather than left to memory: it needs an existing
builder, and it refuses if that builder already got as far as creating the
`annotator` account — past that point `/etc/skel` has already been copied and a
re-run can't produce a correct machine.

**Do the final build without `--reuse`,** so the image annotators get comes off
a clean machine. A `--reuse` build says so in its closing message.

---

## If the build's self-check fails

`install.sh` ends with ten checks and refuses to let you snapshot a machine
that fails any of them. That is the point — "builds cleanly, produces a machine
that does nothing" is only otherwise discovered by a person trying to work.

A failing check leaves the builder VM up so you can look at it:

```bash
gcloud compute ssh annotator-image-builder --zone us-east5-a --tunnel-through-iap
```

`elan runs from PATH` is the one that has actually failed. ELAN's `.deb` puts no
`elan` on PATH — it installs its own tree with a capitalised launcher inside.
`install.sh` asks `dpkg -L` where the package put its executables and wraps the
launcher as `/usr/local/bin/elan`, rather than hard-coding a path that would
change between ELAN releases. If a future release renames the launcher, the
build prints every executable the package installed and tells you which line to
add it to.

---

## If the desktop doesn't answer

**As of `v3` this should not reach you** — `install.sh` now starts the VNC
service during the build and fails if nothing listens on 5901, so a broken
session is a failed build rather than an annotator staring at a viewer that
will not connect. `systemctl is-enabled` was what it checked before, and that
passes for a service that fails the moment it runs.

If it happens anyway, SSH to the machine and work down this list:

```bash
cat /etc/elan-version /etc/elan-launcher     # no elan-launcher => it's a v1 image
systemctl status vncserver@1 --no-pager -l
journalctl -u vncserver@1 --no-pager -n 50
sudo cat /home/annotator/.vnc/*.log          # usually the most specific error
sudo ss -lntp | grep 5901 || echo "nothing on 5901"

# clearest signal of all -- run it by hand, in the foreground
sudo -u annotator HOME=/home/annotator \
  vncserver :2 -localhost yes -SecurityTypes None -geometry 1280x800 -fg
```



`connect.sh` failing with `failed to connect to backend ... port 5901` means
the tunnel reached the machine but nothing is listening. Look at the machine:

```bash
gcloud compute ssh annotate-<annotator> --zone us-east5-a --tunnel-through-iap --command '
  systemctl status vncserver@1 --no-pager -l | head -20
  sudo ss -lntp | grep 5901 || echo "nothing listening on 5901"
  sudo ls -la /home/annotator/ /home/annotator/.vnc/ 2>&1 | head -20
  sudo tail -30 /home/annotator/.vnc/*.log
'
```

An empty `/home/annotator` means the image was built with the account created
before `/etc/skel` was populated — `install.sh`'s self-check catches that now,
but an image built before that check existed will show it. Rebuild.

---

## The one thing not to break

Every kit's `.eaf` records its media twice, absolute and relative, and **both
are written against `CANONICAL_ROOT` in `make_kit.py`** (`/srv/multidata/case`)
— the path every machine mounts a case at. That is the only reason ELAN opens
without a "locate media" dialog on a machine that isn't the one the file was
made on.

If you change `CANONICAL_ROOT`, you must change where `image/install.sh` creates
the staging directory and where `image/desktop/stage-case` puts kits, or every
kit breaks. `tests/test_make_kit.py` fails loudly if the URLs stop being
canonical.

---

## Status

**Tested for real:** `make_kit.py` (a 57 MB kit built from case 261473 — 18 s,
including the 480p transcode), `submit_checks.py` (against real annotated gold,
where it correctly found two empty segments), `pull_submission.py` (local path),
and 27 unit tests in `tests/test_make_kit.py` and `tests/test_submit_checks.py`.

**Not yet run:** everything touching GCP — `gcp/*.sh` and `image/install.sh`.
They're written and syntax-checked, and no project existed to run them against.
Two things in particular are unverified and will need a real attempt:

- **`ELAN_DEB_URL` has no default in the scripts**, because inventing a
  download URL that 404s is worse than a variable that fails loudly.
  `build_image.sh` checks it locally before booting anything.
  `ELAN_DEB_SHA256` is optional: verified when given, and printed when not, so
  a first build hands you the value to pin for the next one. The working values
  for ELAN 7.1 are in the command above.
- **`image/elan_prefs/` is empty.** ELAN's preferences file format wasn't
  confirmed, so rather than ship an XML file ELAN might silently ignore, the
  directory is empty and `install.sh` warns. `image/elan_prefs/NOTES.md` has
  the one-time step: set autosave by hand in ELAN once, copy the file out,
  commit it.

Then work the acceptance checklist in the image design §6 — playback first,
with a real kit, before anyone is onboarded.
