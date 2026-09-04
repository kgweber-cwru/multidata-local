# Annotator Guide — transcribing an encounter in ELAN

Step-by-step instructions for producing one gold transcript.

This guide tells you **what to do**. For *why* any rule exists, see
[transcription_standards.md](transcription_standards.md) — cited as **[S§n]**
throughout. When this guide and the standards disagree, the standards win; tell
whoever maintains them.

> **You work blind.** No machine transcript, no other annotator's file, no
> existing draft. If someone offers you one, decline — it biases the result in a
> way that can't be detected afterward. **[S§9]**

---

## Before you start

You need:

- **ELAN 7.x** installed
- **`elan/template.etf`** — the project template
- Your assigned case's **video** (`.mp4`) and **audio** (`.wav`)
- **Headphones.** Not laptop speakers. You will be distinguishing `uh-huh` from
  `uh-uh`, and that decision changes a patient's answer from yes to no.
- The **cheat sheet** at the bottom of this page, printed or on a second screen

Expect roughly **6–10× real time** for a first pass. A 20-minute encounter is
most of a day. That's normal and it is not a sign you're doing it wrong.

---

## Step 1 — Create the file from the template

**File → New.**

In the dialog:
1. Add **both** media files — the `.mp4` *and* the `.wav`. The video shows you
   who is speaking; the `.wav` gives you a real waveform instead of a
   video-derived guess.
2. Select **`elan/template.etf`** as the template.
3. OK.

You should see six empty tiers: `LEARNER`, `PATIENT`, `PRECEPTOR`,
`ANNOUNCEMENT`, `OUTSIDE_ROOM`, `NOTES`. **[S§4]**

**Save immediately** as `<case_id>.pass1.eaf` in the case's gold folder. Save
often after that.

> **Never create a tier** unless someone genuinely present fits none of the five
> speaker tiers. If that happens, stop and ask — it usually means something about
> the case is unusual and worth recording.

---

## Step 2 — Listen through once, before annotating anything

Play the encounter start to finish at normal speed. Don't type.

You are learning: how many people speak, what each voice sounds like, where the
hard patches are, and whether anything odd happens (equipment noise, an
interruption, hallway bleed).

This costs 20 minutes and saves hours. Voice identification gets dramatically
easier once you know who's in the room.

Jot anything notable straight into `NOTES` as you go.

---

## Step 3 — Segment, one tier at a time

Switch to **Segmentation Mode** (the tab across the top).

**Work one speaker tier at a time, through the whole recording.** Select
`LEARNER`, pass through the encounter marking only where the learner speaks. Then
`PATIENT`. Then `PRECEPTOR`. Then the others if they have any content.

This feels like more listening than doing all speakers at once. It is faster and
more accurate, for two reasons:

- "When does *this one person* speak?" is a far easier perceptual task than
  tracking everyone simultaneously.
- **Overlapping speech comes out correct for free.** Two people talking at once is
  just two tiers with annotations at the same time. You never mark overlap by
  hand. **[S§4]**

### What counts as one segment

**One utterance or breath group** — a natural phrase boundary, usually a few
seconds. *Not* the whole turn. **[S§5]**

If the learner talks for 90 seconds, that's many segments, not one. Long
annotations are miserable to type and to check.

Turn-level boundaries for the diarization reference get rebuilt automatically at
export. You don't need to think about them.

### How precise?

**Bound the speech; don't chase it.** Roughly a tenth of a second is plenty.
Nothing downstream needs better.

**Don't mark pauses.** Silence between segments is already measured by the
timestamps. **[S§5]**

You can **set "delayed mode"** if you find it helpful: it will automatically
place a boundary a number of milliseconds before you actually hit <enter>.
200 ms might be a good place to experiment depending on your reaction time.

### Shortcuts

- The default configuration is to hit **Enter** to mark the beginning and again at the end of a segment. 
- The up-arrow and down-arrow keys will change the tier you are segmenting
- You can drag a segment around in the area above the tiers. If it's selected (green), you can delete with the **Delete** (or **Backspace**) key and pull its edges back and forth. But don't feel you have to unless you think it'll be difficult to transcribe later. (The transcription mode automatically plays the segment for you)
- **Control-space** starts and stops the player
- **space** will play only the sound in the highlighted (blue) part of the audio

---

## Step 4 — Transcribe

Switch to **Transcription Mode**. It shows your segments in a column and plays
each one as you land on it, looping while you type. This is much faster than
typing in the timeline.

Work tier by tier, same order as Step 3.

### The rules while typing

**Type exactly what you hear.** Every false start, repetition, and grammatical
error stays. Do not tidy. The clean version is generated later — cleaning at the
keyboard cannot be undone. **[S§6]**

**Type real names as you hear them.** "Hi, I'm Jamie" is transcribed as *Hi I'm
Jamie*. Do **not** write `[LEARNER_NAME]`. Redaction happens mechanically later,
and doing it by hand creates errors and scores against providers that were
correct. **[S§8]**

**Loop the segment as many times as you need.** Three or four passes on a hard
one is normal.

### Work in ~30-second bites

Segment about 30 seconds, transcribe it, then move on. Don't segment the entire
recording and then transcribe the entire recording — you'll lose your sense of
the audio and rework more.

Nothing in the file records these bites; it's just how to pace the work.

---

## Step 5 — When you're not sure

**Never guess.** A flagged unknown is recoverable; a confident error is not.

| Situation | Write |
|---|---|
| Can't make out any of it | `[unintelligible]` |
| Have a decent guess | `[unintelligible: pressure?]` |
| Can't tell `uh-huh` from `uh-uh` | `[unintelligible: uh-huh?]` |
| Don't know the medical term | best phonetic guess in `[unintelligible: ...?]`, then flag it |

Uncertain spans are excluded from scoring on both sides, so marking one costs
nothing. Guessing wrong costs a lot. **[S§6]**

### The one that matters most

**`uh-huh` means yes. `uh-uh` means no.** One letter apart, opposite meanings, and
a patient's answer to a clinical question can hinge on it.

- `uh-uh` (no) — glottal catch between the syllables, pitch falls then rises
- `uh-huh` (yes) — audible /h/, pitch rises

**If you can't tell, listen to what happens next.** The other speaker's response
almost always settles it. Still unsure — mark it uncertain. **[S§6]**

---

## Step 6 — Use the NOTES tier

Drop a note whenever something affects transcription quality: a noise burst, a
very quiet stretch, heavy crosstalk, an unfamiliar accent, a mic problem, an
interruption.

Free text, rough timing, **not exhaustive**. This is how anyone later explains why
a case scored the way it did. **[S§4]**

Don't use NOTES for transcription content.

---

## Step 7 — Finish and hand off

1. **Check every tier.** Any segment still empty? Either transcribe it or delete
   it — empty segments become phantom turns in the diarization reference.
2. **Spot-check five random segments** against the audio.
3. **Save** as `<case_id>.pass1.eaf`.
4. **Tell the project lead it's done.**

Once you report it complete, **pass 1 is frozen**. Don't reopen it to fix
something you thought of later — report the concern instead. It gets handled in
adjudication, where the change is recorded. **[S§9]**

> **File handling:** your `.eaf` contains real names. Keep it on managed project
> storage. Never email it, never put it in a personal cloud folder, never commit
> it. **[S§8]**

---

## Cheat sheet

### Filled pauses — the only three
```
uh      um      hmm
```
Not `er`, not `erm`, not `ah`. **[S§6]**

### Backchannels — write them, they mean something
```
mm-hmm   yes        uh-uh   no
uh-huh   yes        mm-mm   no
huh?     "say that again?"
```

### Marks
```
so:                stretched sound (one colon, however long it is)
par-               cut-off word or false start
I I I went         repetition — just type it, no markup
[unintelligible]           couldn't hear it
[unintelligible: word?]    best guess
```

### Optional, when it's interesting
```
[laugh]   [cough]   [sigh]   [throat clear]
```

### Numbers — as spoken
```
✅ one twenty over eighty        ❌ 120/80
✅ fifteen milligrams            ❌ 15 mg
```

### Never
```
❌ [LEARNER_NAME]      — type the real name
❌ overlap markers     — separate tiers handle it
❌ pause marks         — timestamps handle it
❌ tidying grammar     — verbatim means verbatim
❌ guessing            — mark it uncertain
```

### Domain terms
Spell from `benchmarks/configs/glossary.yaml`. Term missing? **Add it** — that's
how the list grows.

---

## Common mistakes

| Mistake | Why it hurts |
|---|---|
| Segmenting all speakers in one pass | Much harder, and you lose overlap for free |
| One segment per whole turn | Painful to type, painful to review |
| Cleaning up speech while typing | Irreversible; the clean version is generated |
| Writing `[LEARNER_NAME]` | Breaks scoring and hides what was actually said |
| Guessing a term instead of flagging | Turns a free non-answer into a counted error |
| Chasing exact word boundaries | Nothing uses that precision |
| Reopening a frozen pass 1 | Destroys the blind-pass property |

---

## Questions

Anything this guide doesn't cover, ask rather than improvise — an improvised
convention applied across a whole case is expensive to undo, and usually means the
standards need a fix that helps every annotator. **[S§12]**
