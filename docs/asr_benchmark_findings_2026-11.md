# ASR benchmark: first findings (November 2026)

## What was measured

We sent five recordings to nine ASR configurations (a configuration is a
vendor, model and settings) and scored each transcript against a human gold
transcript with word error rate (WER). We report two WER values. **L0**
counts every word, fillers included. **L1** ignores fillers ("um", "uh") and
cut-off words. Before scoring, names are redacted on both sides and numbers
are put in one form ("one twenty" = "120"). Scores are pooled across
recordings, weighted by gold word count.

We also measured how well each engine told the speakers apart and gave each
person's words to the right person ("diarization"), with DER (diarization error rate) and
cpWER (a WER that penalizes words given to the wrong person).

| Recording | Scenario | Audio | People talking | Gold words |
|---|---|---|---|---|
| 252848 | 2025 long station | 64.0 min | 35.6 min (56%) | 6,002 |
| 261456 | Wellness (alcohol) | 19.9 min | 17.3 min (87%) | 3,229 |
| 261505 | Wellness (alcohol) | 20.0 min | 16.2 min (81%) | 3,318 |
| 261473 | Chest pain | 20.2 min | 3.5 min (17%) | 907 |
| 261498 | Chest pain | 20.1 min | 4.4 min (22%) | 949 |
| **Total** | 3 scenarios | **144 min** | **77 min (53%)** | **14,405** |

"People talking" is the total length of the speech turns, not counting the
recorded announcements. The rest is silence (physical exam, note writing,
waiting). Each engine processed the full audio, silence included. The cloud
runs cost about $5 at list prices. Only Google billed us: ElevenLabs and
AssemblyAI ran on free demo credits, and Whisper on a local server.

## Results

| Engine and setting | L0 WER | L1 WER | Substituted | Missed | Added | Range across recordings (L0) |
|---|---|---|---|---|---|---|
| AssemblyAI universal-3-5-pro | **0.162** | **0.165** | 4.8% | 5.8% | 5.7% | 0.16–0.19 |
| AssemblyAI + Medical Mode | 0.162 | 0.165 | 4.7% | 5.8% | 5.7% | 0.16–0.19 |
| Gemini 3.5 Transcribe | 0.169 | 0.171 | 6.0% | 5.1% | 5.8% | 0.15–0.24 |
| ElevenLabs scribe_v3 | 0.187 | 0.188 | 5.6% | 3.8% | 9.3% | 0.16–0.23 |
| ElevenLabs scribe_v2_medical | 0.194 | 0.194 | 5.6% | 3.9% | 9.9% | 0.17–0.26 |
| Google Chirp 3 | 0.207 | 0.209 | 6.9% | 4.6% | 9.1% | 0.18–0.33 |
| faster-whisper medium (local) | 0.243 | 0.211 | 5.6% | 16.1% | 2.7% | 0.20–0.35 |
| WhisperX large-v3 (local) | 0.267 | 0.240 | 5.4% | 18.2% | 3.1% | 0.25–0.33 |
| WhisperX disfluent large-v3 (local) | 0.265 | 0.265 | 5.8% | 16.1% | 4.5% | 0.20–0.37 |

Substituted, missed and added are shares of gold words and sum to L0 WER.

**AssemblyAI is best on every recording** and the most consistent. It adds
about as many words as it misses, and substitutes fewer than any other
engine. **Medical Mode changes nothing** (identical to three decimals) and
costs $0.15/hour more.

**Gemini 3.5 Transcribe** (on Vertex AI) is a close second, and best on
252848 (0.150). It keeps 55 of gold's 190 back-channel utterances ("mm-hmm", "uh-huh", etc). AssemblyAI keeps 9.
It adds and misses words at about AssemblyAI's rate, so the gap between them
is substitutions (6.0% against 4.8%). Its speaker attribution is weaker
(see "Who said what"). Google services are covered by the CWRU enterprise
privacy contract.

**ElevenLabs is third.** Its BAA model (`scribe_v2_medical`) is 0.7 points
behind its general model.

**Google Chirp 3 is uneven.** It scored 0.18 on the wellness recordings and
0.30–0.33 on the two chest-pain recordings. It also took 4–9 minutes per
recording; the others took under 90 seconds.

**The local Whisper engines trail by 6–10 points.** Most of their errors are
missed speech (16–18% of gold words). Their substitution rates are the same
as the cloud engines'.

## What the numbers don't show

**Back-channels.** Gold has 190 "mm-hmm", "uh-huh" and similar. AssemblyAI
writes 9 of them, Google 32, Gemini 55, ElevenLabs 115. AssemblyAI often
writes "yeah" and drops the rest. In a clinical encounter "uh-huh" (yes) and
"uh-uh" (no) can be answers, so the best-scoring engine loses information
that downstream analysis may need. WER counts each as one missed word, so
the scores barely reflect this.

**Fillers.** AssemblyAI keeps 579 of gold's 587 fillers. ElevenLabs and
Google write more fillers than gold (634–656). faster-whisper and WhisperX
remove them by design (18 and 74 kept), so L1 is the fair score for them.
WhisperX disfluent keeps them (552).

**Some added words may be real speech.** ElevenLabs and Google produce
about 5% more words than gold, and some may be speech the annotator missed.
On 252848 at 44:10–45:35, gold has 3 words where our diarization finds
speech and AssemblyAI writes 64. Until a second annotator transcribes the
same audio, we can't tell how much of the Added column is engine error and
how much is quiet or overlapping speech missing from gold.

**Google struggles on the mostly silent recordings.** The chest-pain
recordings are 78–83% silence. There Google's substitutions double (12–13%,
against 6–7% elsewhere) and its insertions reach 12–14%. Two recordings are
not enough to say why.

**WhisperX disfluent misses a fifth of the speech on three recordings**
(21–25% of gold words on 261456, 261505 and 261473, against 9–10% on the
other two). Two of the three are camera-16 recordings, whose audio drops out
during recording gaps; the third is a chest-pain recording. We don't yet
know why.

## Who said what

Speaker attribution is scored two ways. **cpWER** is WER with each engine
speaker's words matched to one gold person, so words given to the wrong
person count as errors. **DER** is the share of speaking time given to the
wrong person, missed, or invented, built from each engine's word timings.
The 0.5 s on either side of each gold boundary is not scored, because
hand-placed boundaries are that loose. 

We asked each provider to supply diarization with the transcript. The local
Whisper transcripts were diarized with a Python module called `pyannote`. 
We also scored AssemblyAI and Gemini transcripts with `pyannote` diarization. 
"Cost of attribution" is cpWER minus WER, in points.

| Engine | WER | cpWER | Cost of attribution | DER |
|---|---|---|---|---|
| AssemblyAI + Medical Mode | 0.162 | **0.169** | +0.7 | 0.282 |
| AssemblyAI | 0.162 | 0.171 | +0.9 | 0.280 |
| ElevenLabs scribe_v3 | 0.187 | 0.201 | +1.4 | 0.304 |
| Gemini 3.5 Transcribe | 0.169 | 0.259 | +9.0 | 0.260 |
| Google Chirp 3 | 0.207 | 0.268 | +6.1 | 0.246 |
| AssemblyAI words, pyannote speakers | 0.162 | 0.275 | +11.3 | 0.292 |
| ElevenLabs scribe_v2_medical | 0.194 | 0.284 | +9.0 | 0.342 |
| Gemini words, pyannote speakers | 0.169 | 0.287 | +11.8 | 0.249 |
| faster-whisper medium (local) | 0.243 | 0.343 | +10.0 | **0.223** |
| WhisperX large-v3 (local) | 0.267 | 0.354 | +8.7 | 0.364 |
| WhisperX disfluent large-v3 (local) | 0.265 | 0.367 | +10.2 | 0.390 |

**AssemblyAI almost always gives words to the right person**, with or
without Medical Mode. ElevenLabs fails rarely but badly: on 261456 its
medical model gave 2,360 words to one speaker where gold has the learner and
patient talking about equally, so one of them is lost entirely (cpWER 0.60
there).

**Gemini's** cpWER weakness comes mostly from one recording. Vertex accepts files of at
most 15 minutes, so longer recordings are sent in overlapping pieces and
speakers are matched across them; that matching worked on all five. On
261505 the preceptor joins at 15:07, and within that piece Gemini gave 553
of the preceptor's words to the learner's label (cpWER 0.43 there). On the
other four recordings Gemini's cpWER ranges from 1.7 points better than
AssemblyAI's to 5.5 points worse, mostly from patient speech split off
under extra labels.

Google notes that diarization with three or more speakers is experimental.
This may evolve as the model matures.

**faster-whisper with pyannote** has the best DER, so `pyannote` finds
speaker time well. But the local transcripts it labels miss too many words
for that to help, and putting its labels on AssemblyAI's or Gemini's words
makes cpWER worse for both (0.171 to 0.275, and 0.259 to 0.287).

## How much to trust this

- **Five recordings, three scenarios, small annotator pool.** The largest scenario
  in the corpus (2026 long station, abdominal pain: 20 of 57 recordings) has
  no gold yet. Differences under about 2 points could easily reverse with
  more recordings.
- **Gold quality varies.** Kate made the final pass on all five; two had no
  formal second-pass assignment.
- **Names.** Redaction misses some surname and short-name forms, so engines
  that hear those names take a small insertion penalty.
- **Data handling.** ElevenLabs accepted our audio only with its logging on for its free tier 
  (it keeps the audio). This was approved for the redacted gold recordings only.

## Recommended next steps

1. **Broaden gold.** Finish the two seeded pass-1 kits now assigned (261466,
   long-station abdominal pain; 259392, the only CSE 2A recording). The
   seizure recordings already in progress add a fourth scenario.
2. **Widen the annotator pool.** Only two of the five gold transcripts were first
   drafted by someone other than Kate, and she made the final pass on all
   five. Each needs a second reviewer.
3. **Treat AssemblyAI universal-3-5-pro (without Medical Mode) as the lead
   candidate.** Its BAA terms must be confirmed before any audio beyond the
   gold set is sent. Keep **Gemini** as the runner-up on words and
   back-channels, and watch its speaker attribution on new gold: one merge
   in five recordings could be a one-off or a pattern.
4. **Decide whether downstream work needs back-channel utterances.** A prompt asking
   AssemblyAI to keep them changed nothing (10 of 190), so if they matter
   they have to come from another engine.
5. **Measure how much two humans disagree.** Two careful annotators
   transcribing the same audio will not match word for word, so an engine
   can't be expected to score better than that. Have a second annotator
   transcribe one or two excerpts without seeing the existing gold, and
   score the two human transcripts against each other. Their WER is the
   floor: engine differences smaller than it don't mean much. This also
   shows how many added words are real speech. Start with 252848 at
   44:10–45:35.
6. **Drop Google Chirp 3 and AssemblyAI Medical Mode** from further runs
   unless new gold changes the picture. Keep one ElevenLabs model as the
   runner-up.
7. **Test giving vendors a glossary** to help models select relevant words
once gold covers more vocabulary.
--

KGW 2026-11-17