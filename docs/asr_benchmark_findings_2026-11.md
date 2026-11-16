# ASR benchmark: first findings (November 2026)

*Preliminary. Five gold recordings, one gold transcript each, no noise floor
yet. Read the numbers as a first ranking, not a verdict.*

## What was measured

Eight engine settings transcribed the five gold recordings (simulated
encounters in our sim center) and were scored against human gold transcripts
with word error rate (WER). **L0** counts every word, fillers included; **L1**
ignores fillers ("um", "uh") and cut-off words. Both sides have names
redacted and numbers put in one form ("one twenty" = "120") before scoring.
Scores are pooled across recordings, weighted by gold word count.

| Recording | Scenario | Audio | People talking | Gold words |
|---|---|---|---|---|
| 252848 | 2025 long station | 64.0 min | 35.6 min (56%) | 6,002 |
| 261456 | Wellness (alcohol) | 19.9 min | 17.3 min (87%) | 3,229 |
| 261505 | Wellness (alcohol) | 20.0 min | 16.2 min (81%) | 3,318 |
| 261473 | Chest pain | 20.2 min | 3.5 min (17%) | 907 |
| 261498 | Chest pain | 20.1 min | 4.4 min (22%) | 949 |
| **Total** | 3 scenarios | **144 min** | **77 min (53%)** | **14,405** |

"People talking" is the union of gold's speech turns, excluding the
recorded announcements; the rest is silence (physical exam, note writing,
waiting). Each engine processed the full audio, silence included. The cloud
runs cost about $5 in all at list prices.

## Results

| Engine and setting | L0 WER | L1 WER | Substituted | Missed | Added | Range across recordings (L0) |
|---|---|---|---|---|---|---|
| AssemblyAI universal-3-5-pro | **0.162** | **0.165** | 4.8% | 5.8% | 5.7% | 0.16–0.19 |
| AssemblyAI + Medical Mode | 0.162 | 0.165 | 4.7% | 5.8% | 5.7% | 0.16–0.19 |
| ElevenLabs scribe_v3 | 0.187 | 0.188 | 5.6% | 3.8% | 9.3% | 0.16–0.23 |
| ElevenLabs scribe_v2_medical | 0.194 | 0.194 | 5.6% | 3.9% | 9.9% | 0.17–0.26 |
| Google Chirp 3 | 0.207 | 0.209 | 6.9% | 4.6% | 9.1% | 0.18–0.33 |
| faster-whisper medium (local) | 0.243 | 0.211 | 5.6% | 16.1% | 2.7% | 0.20–0.35 |
| WhisperX large-v3 (local) | 0.267 | 0.239 | 5.4% | 18.2% | 3.1% | 0.25–0.33 |
| WhisperX disfluent large-v3 (local) | 0.265 | 0.264 | 5.8% | 16.1% | 4.5% | 0.20–0.37 |

Substituted, missed and added are shares of gold words; they sum to L0 WER.

**AssemblyAI is best on every recording**, and the most consistent. Its
errors are balanced: as many words added as missed, and fewer substitutions
than any other engine. **Medical Mode changes nothing** (identical to three
decimals) at +$0.15/hour.

**ElevenLabs is second.** The model it sells with a BAA (`scribe_v2_medical`)
is 0.7 points behind its general model.

**Google is third on average but the least predictable**: 0.18 on the two
wellness recordings, 0.30–0.33 on the two chest-pain recordings. It also
costs about four times as much per hour as the other two and took 4–9
minutes per recording against under 90 seconds for the others.

**The local Whisper engines trail by 6–10 points**, and their errors are
almost all *missed* speech (16–18% of gold words), not mishearing; their
substitution rates match the cloud engines'.

## What the numbers don't show

**Back-channels.** Gold has 187 "mm-hmm", "uh-huh" and similar. AssemblyAI
writes 9 of them, Google 32, ElevenLabs 114. AssemblyAI writes "yeah" often
and drops the rest. In a clinical encounter "uh-huh" (yes) and "uh-uh" (no)
can be answers, so the best-scoring engine loses something that matters for
downstream analysis. WER counts each one as a single missed word, so this
barely moves the scores.

**Fillers.** AssemblyAI keeps 579 of gold's 587 fillers; ElevenLabs and
Google write *more* fillers than gold (634–656). faster-whisper and WhisperX
remove them by design (18 and 74 kept), so L1 is their fair score.
`whisperx_disfluent` keeps them (552).

**Added words may not all be errors.** ElevenLabs and Google produce about
5% more words than gold. Some are invented, but some are probably real speech
the annotator left out: on 252848, at 44:10–45:35, gold has 3 words where
our own diarization finds speech and AssemblyAI writes 64. Until a second
annotator transcribes the same audio, we can't say how much of the
"Added" column is engine error and how much is gold missing quiet or
overlapping speech.

**Google struggles on the mostly-silent recordings.** The chest-pain
recordings are 78–83% silence. Google's substitutions double there (12–13%
against 6–7% elsewhere) and its insertions reach 12–14%. Two recordings
are not enough to say why.

**WhisperX disfluent misses a fifth of the speech on three recordings**
(21–25% of gold words on 261456, 261505 and 261473, against 9–10% on the
other two). Two of those three are the camera-16 recordings whose audio
drops out during recording gaps; the third is a chest-pain recording. Not
yet understood.

**Speakers aren't scored.** Who said what (diarization error rate) isn't
measured yet. All three cloud engines labelled two main speakers correctly
on the 60-second smoke clip, and Google needed a fix to keep speakers
consistent across the pieces it is sent in, but there is no number for any
engine.

## How much to trust this

- **Five recordings, three scenarios, one annotator.** The largest scenario
  in the corpus (2026 long station, abdominal pain: 20 of 57 recordings) has
  no gold yet. Differences under about 2 points are likely within noise.
- **No noise floor.** No audio has two independent human transcripts, so
  human-to-human disagreement is unknown.
- **Gold quality varies.** Kate made the final pass on all five; two had no
  formal second-pass assignment.
- **Names.** Redaction misses some surname and short-name forms, so engines
  that hear those names take a small insertion penalty.
- **Cloud models change silently.** These are snapshots from 16 November
  2026.
- **Data handling.** ElevenLabs only accepted our audio with its logging on
  (it keeps the audio); this was approved for the gold recordings only.

## Recommended next steps

1. **Treat AssemblyAI universal-3-5-pro (plain, no Medical Mode) as the
   lead candidate**, subject to confirming its BAA terms before any audio
   beyond the gold set is sent.
2. **Look into the back-channel loss before committing.** Listen to a
   sample of AssemblyAI's missed "mm-hmm"/"uh-uh", and try one prompt
   setting asking it to keep them (about $0.50 across the gold set). If
   they can't be recovered, decide whether downstream work needs them.
3. **Establish a noise floor.** Have a second annotator transcribe one or
   two excerpts blind. This also settles how many "added" words are real.
   Listen first to 252848 at 44:10–45:35.
4. **Broaden gold.** Finish the two seeded pass-1 kits now assigned (261466,
   long-station abdominal pain; 259392, the only CSE 2A recording); the
   seizure recordings already in progress add a fourth scenario.
5. **Score speaker attribution** (DER against the gold turns) before
   choosing, since transcripts are used per speaker.
6. **Drop Google Chirp 3 and AssemblyAI Medical Mode** from further runs
   unless new gold changes the picture; keep one ElevenLabs model as the
   runner-up.
7. **Hold the glossary test** until gold covers more vocabulary.
