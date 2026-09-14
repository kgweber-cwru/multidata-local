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
# annotation/gcp/connect.sh, the VNC password, and the ready-made
# `--annotator` line it prints. Walk them through it once -- 15 minutes. On a
# Mac they install only the Google Cloud CLI; Screen Sharing is already there.
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

## How the desktop is reached, and why not the obvious way

The desktop listens on **`127.0.0.1:5901` on the remote machine**, and that is
not a choice: TigerVNC refuses to expose a server with `-SecurityTypes None` to
the network, and binds loopback whatever flags you pass it. No password means
loopback, full stop — the two settings are coupled.

So `connect.sh` tunnels IAP to **port 22**, the path every other script here
already uses, and lets SSH forward a local port to that remote loopback
address. Pointing IAP straight at 5901 cannot work: IAP connects to the VM on
its *internal interface*, and nothing is listening there.

This is the better arrangement regardless:

- **No VNC port open anywhere.** The firewall only needs `tcp:22` from IAP's
  range — `setup_project.sh` opens nothing else.
- **The real authentication is Google IAM**, against a named person.

There *is* a VNC password, generated per image and printed at the end of the
build. It exists for the client's sake rather than the threat model's: **macOS
Screen Sharing cannot connect to a server offering no authentication** — it
prompts and then hangs — so giving it a password means Mac annotators need no
viewer installed at all. Keep it out of the repo; pass it back as
`VNC_PASSWORD` to rebuild an identical image.

Annotators therefore need SSH access to their own machine, which `new_vm.sh`
prints the grants for. That gives them a shell as well as a desktop; fine under
the decision that annotators are trusted (image design §7.4).

### Connecting from a machine that isn't set up for this project

`connect.sh` guesses the annotator name from your local username and the project
from your current `gcloud` config. Both are often wrong on a second computer, so
both can be overridden:

```bash
./connect.sh --annotator kxw680 --project cwru-sim-center-data
```

A name with no machine behind it now says so, and lists the machines that do
exist, rather than failing with "failed to connect to backend".

### If it still won't connect

```bash
# on the machine
sudo ss -lntp | grep 5901            # want 127.0.0.1:5901
systemctl status vncserver@1 --no-pager -l
sudo cat /home/annotator/.vnc/*.log  # usually the most specific error

# clearest signal of all -- run it by hand, in the foreground
sudo -u annotator HOME=/home/annotator \
  vncserver :2 -localhost yes -geometry 1280x800 -fg
```

A viewer that prompts for a password and then spins usually means the server
has no password file (`sudo ls -l /home/annotator/.vnc/passwd`) — macOS Screen
Sharing cannot complete a no-authentication connection.

`cat /etc/elan-version /etc/elan-launcher` first — no `elan-launcher` means the
machine came from a `v1` image, built before several of these fixes.

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

**The cause, three times running, was `-localhost yes` on the VNC server.**
It looks like the cautious choice and it is simply broken: IAP TCP forwarding
does not terminate on the VM. `gcloud` opens a WebSocket to Google, and Google
connects to the machine on its **internal interface address** — so a server
bound only to `127.0.0.1` is unreachable through the tunnel, which is why sshd,
reached by the same tunnel, listens on `0.0.0.0`. What keeps 5901 safe is the
firewall (IAP's range only) and the absence of any external IP.

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

## Status — 2026-09-14

**Run for real, end to end.** A machine was built, connected to, and a kit
staged onto it.

| | |
|---|---|
| `make_kit.py` | 57 MB kit from case 261473 in 18 s, including the 480p transcode. Media URLs verified canonical. |
| `submit_checks.py` | Run against real annotated gold, where it correctly found two empty segments. 27 unit tests. |
| `pull_submission.py` | Local path only — not yet against a real submission in the bucket. |
| `setup_project.sh` | Run. Firewall, Private Google Access, bucket, service account. |
| `build_image.sh` | Run, several times. `annotator-v1` and `v2` built. |
| `image/install.sh` | Run. Its self-check caught a real bug (`elan` not on PATH) before that image was used. |
| `gcp/new_vm.sh`, `push_kit.sh` | Run. |
| `gcp/connect.sh` | **Working** — desktop reached from a Mac over the SSH-forwarded tunnel. |
| `gcp/delete_vm.sh` | Not yet run. |

**What it cost, and what it bought.** Getting from "written" to "working" took
several rounds, and every one of them is now a check rather than a lesson: the
package list, the ELAN launcher path, the `/etc/skel` ordering, the `vncserver`
path, SSH readiness, whether the desktop *starts* rather than merely being
enabled, and whether it's bound where the tunnel can reach it. `install.sh`
refuses to snapshot a machine that fails any of them. That's the reason a v3
build should be uneventful.

**The one thing still unknown, and the reason all of this exists:** whether
ELAN plays the kit's proxy video and wav together, and whether looping a
segment holds up over the connection. Nothing so far has tested that — it
needs a person, a real kit, and an hour.

**Also still open:** the latency measurement that settles whether delayed mode
needs setting (image design §5), and `delete_vm.sh`.

Then work the acceptance checklist in the image design §6 — playback first,
with a real kit, before anyone is onboarded.
