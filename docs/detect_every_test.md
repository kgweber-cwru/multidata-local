# Test: running the person detector every Nth frame

Run 2026-11-01T14:32:35 to 2026-11-01T16:44:21 on tofino by `scripts/test_detect_every.py`; all numbers in `logs/detect_every_test.json`.

**Verdict: `--detect-every 1`** (no stride passed; keep every frame).

Thresholds were fixed before the run: median keypoint shift <= 2.0 px, 95th percentile <= 8.0 px (worst clip), missed and extra people <= 1% of person-frames, all measured against running the detector on every frame.

| Detector runs | Median shift (px) | Worst clip 95th pct (px) | Missed people | Extra people | Speed vs every frame | Pass |
|---|---|---|---|---|---|---|
| every 2 frames | 0.00 | 13.6 | 1.05% | 1.09% | 1.41x | no |
| every 3 frames | 0.75 | 18.4 | 1.62% | 1.62% | 1.64x | no |

## Clips

One per room: the video with the most people, at its 300 s of most movement (the hardest case for reused detector boxes).

| Room | Recording/camera | Start (s) | Every 2: median / p95 px, missed | Every 3: median / p95 px, missed |
|---|---|---|---|---|
| 1 | 260658/337 | 1022 | 0.00 / 4.2, 1.50% | 0.25 / 5.9, 2.41% |
| 2 | 261465/37 | 2509 | 0.00 / 5.6, 0.12% | 0.49 / 7.7, 0.23% |
| 3 | 261412/291 | 418 | 0.00 / 7.3, 0.81% | 0.83 / 9.9, 1.35% |
| 4 | 261413/36 | 1058 | 0.00 / 12.8, 2.15% | 1.28 / 17.4, 3.31% |
| 5 | 261414/34 | 1127 | 0.00 / 13.6, 1.67% | 1.37 / 18.4, 2.51% |
| 6 | 252850/4 | 104 | 0.00 / 4.3, 0.34% | 0.75 / 5.0, 0.48% |
| 7 | 252851/33 | 0 | 0.00 / 6.1, 0.64% | 0.87 / 7.6, 0.75% |
| 8 | 255098/19 | 11 | 0.00 / 4.5, 0.03% | 0.70 / 6.0, 0.03% |
| 9 | 261508/23 | 291 | 0.00 / 4.1, 1.23% | 0.44 / 5.5, 1.83% |
| 10 | 261041/10 | 379 | 0.00 / 4.2, 0.87% | 0.55 / 5.8, 1.30% |
| 11 | 261456/16 | 86 | 0.00 / 2.1, 0.01% | 0.25 / 2.7, 0.03% |
| 12 | 259392/42 | 572 | 0.00 / 12.8, 2.27% | 1.05 / 17.2, 3.58% |
| 13 | 261498/290 | 325 | 0.00 / 8.7, 1.47% | 1.30 / 11.7, 2.32% |
| 14 | 261320/9 | 176 | 0.00 / 12.0, 1.90% | 1.21 / 15.6, 2.88% |
| 15 | 261515/22 | 644 | 0.00 / 3.8, 0.13% | 0.22 / 5.4, 0.18% |

Caveat: this measures change from the every-frame run, not error against hand-marked truth. A passing stride gives the same answer as every frame within these tolerances; it doesn't say either is right.
