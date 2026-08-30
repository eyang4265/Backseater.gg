# Player rating version two: accuracy redesign

## Decision

Replace the current single-match lobby z-score model. Its weights can be tuned,
but tuning cannot fix its main error: it has no stable reference population.
Version two should compare a player with historical expectations for the same
role and champion, preserve the magnitude of direct-opponent differences, and
calibrate the final number against reviewed matches.

The score should answer:

> Given this champion, role, game length, patch, and available team context, how
> much better or worse did this player perform than expected?

It should not answer merely who accumulated the largest box-score numbers in one
lobby, and it should not be a disguised win-probability score.

## Why version one is inaccurate

### 1. Ten players are not a valid normalization population

Every metric is standardized against the current lobby. Many role-specific
metrics have only two observations. With two values, ordinary z-scoring reduces
almost every non-tie to `+1` and `-1`, whether the difference is 1 CS or 80 CS.
The model therefore discards the size of exactly the role-opponent advantages it
was intended to measure.

Lobby normalization also makes a player's rating unstable. If an unrelated
player's damage, vision, or CC changes, everyone else's mean and standard
deviation change. The same performance can receive a different score solely
because the other eight players were unusually strong or weak.

### 2. Players in different roles and champion archetypes are compared directly

Raw damage, healing, CC, mitigation, vision, and objective damage have different
distributions for an enchanter, engage support, tank, assassin, control mage, and
marksman. Role weights do not solve this because the metric is normalized before
the role weight is applied. For example, a tank top and an ADC still enter the
same damage-per-minute distribution.

### 3. Correlated facts are counted repeatedly

Gold at 10, gold at 15, XP at 10, XP at 15, CS at 10, CS at 15, GPM, and CS/min
mostly describe the same economy lead. Damage per minute, damage share, gold
share, kills, bounty, and multikills overlap heavily. A fixed weighted sum treats
these as independent evidence and can make one snowball dominate several
buckets.

### 4. Several metrics reward opportunity more than execution

Objective participation, structure damage, kill participation, vision score,
and even damage are heavily constrained by team control and game state. A player
on the winning team gets more safe opportunities. Removing the `win` boolean
does not remove this outcome leakage.

Conversely, team-share statistics are compositional: one teammate's share can
rise only by reducing another teammate's share. High share is not automatically
good when the team total is low.

### 5. Fixed weights are opinions, not calibration

The current weights and grade cutoffs were not fitted or validated against a
labeled corpus. Invariant tests prove that the arithmetic is deterministic and
bounded; they do not prove that the ordering is accurate.

### 6. Missing timelines change the meaning of the score

Renormalizing over remaining metrics makes a Limited score a different model,
not merely a less certain estimate of the Full score. Two identical box scores
can move substantially when timeline data happens to be available.

### 7. A 0–10 clamp hides uncertainty

Clamping an uncalibrated linear composite produces authoritative-looking
numbers. It does not establish that a 7.5 in one match represents the same
quality as a 7.5 in another patch, role, or champion.

## Implementation status

Version 1.1 (`bot_app/rating.py`, `bot_app/rating_baselines.py`) lands the
part of the version-two model that does not require a fitted or hand-labelled
corpus. Measured over the 356 rateable matches in `json/matches.sqlite`:

| | before | after |
|---|---|---|
| systematic role bias (max − min mean score by role) | 1.22 pts | 0.03 pts |
| grade distribution | S+ 0%, S 0.1%, C+D 82% | S+ 2%, S 4%, A 9%, B 20%, C 39%, D 21%, F 6% |
| split-half reliability (Spearman–Brown, 20 splits) | 0.72 | 0.81 |
| spread of per-lobby mean scores | 0.05 | 0.26 |

**Done**

- §1 corpus: `rating_baselines` stores `(patch, position, metric)` sufficient
  statistics only — never a per-game row, and never player identity.
  `lane_matchups.collect.harvest` funds it from match/timeline pairs it was
  already fetching; `backfill_rating_baselines` seeds the box-score metrics
  from cached matches at zero request cost.
- §2 baselines, at the queue+role level with a hard sample threshold. The
  champion and matchup levels, and the empirical-Bayes shrinkage between
  them, are **not** implemented — a thin `(role, metric)` falls back to lobby
  z-scoring instead of shrinking toward a parent.
- §3 direct-opponent magnitude: two-player metrics no longer collapse to
  `±1`, because the denominator is the population's spread rather than the
  two-sample one.
- §4, partially: checkpoint levels at 10 and 15 minutes were near-duplicates,
  so economy now scores the 10-minute level and the 10→15 *swing*. The
  `playmaking` metric summed three overlapping Riot counters — solo,
  outnumbered and multi kills — letting one takedown count three times; it is
  now two separately weighted metrics.
- §7 calibration: `SCORE_SPREAD` and `GRADE_THRESHOLDS` are derived from the
  observed composite distribution rather than assumed. See their docstrings
  for how to re-derive them when the metric set changes.
- Duration independence: every accumulating total is now per-minute, which a
  pooled corpus requires and lobby normalisation had hidden.
- A dead metric: `enemy_jungle_monsters` read `neutralMinionsKilledEnemyJungle`,
  a field Match-V5 does not send. It was a constant zero for every jungler, so
  6% of the jungle economy bucket measured nothing. It now reads
  `enemyJungleMonsterKills` / `totalEnemyJungleMinionsKilled`.

**Not done**

- §4's full residualization, §5's team-state controls, §6's fitted weights,
  and §8's uncertainty-aware presentation. Bucket weights are still the
  hand-chosen opinions §5 objects to.
- §2's champion-level and matchup-level baselines.
- The §339 rollout plan's shadow mode and reviewer tooling. This change was
  validated against the split-half reliability criterion above rather than by
  human labels.

The scope and honesty note below still stands in full: this is a performance
estimate, not a measurement of skill.

## Version-two model

### 1. Build a historical reference corpus

Persist anonymized sufficient statistics from eligible completed matches. A
baseline row needs only:

- patch, queue, role, champion, game-duration band, and side;
- the extracted metric values and missing-value mask;
- direct role-opponent metric differences;
- enough team totals to derive shares and game-state controls.

Do not persist player identity in the model corpus. Keep separate model versions
per major data/feature definition and retain the exact training window in model
metadata.

Use recent patches with exponential time decay. Begin with ranked Solo/Duo and
Flex as separate queue populations. Do not mix ARAM or Arena into this model.

### 2. Use hierarchical baselines with shrinkage

For each metric, estimate an expected distribution at these levels:

1. queue + role;
2. queue + role + champion;
3. queue + role + champion + patch;
4. matchup-specific adjustment when the sample is sufficient.

Small champion, patch, or matchup samples must shrink toward the broader role
baseline instead of producing volatile estimates. A straightforward empirical
Bayes mean is sufficient for the first implementation:

```text
shrunk_mean = (n * local_mean + k * parent_mean) / (n + k)
```

Estimate scale robustly with median absolute deviation, falling back through the
same hierarchy. Store sample size and effective sample size for every baseline.

Normalize each value against its historical expectation, not the current lobby:

```text
residual = observed - expected
standardized = clip(residual / robust_scale, -4, 4)
```

Use a percentile transform for highly zero-inflated values such as steals and
solo kills. A zero should be neutral when zero is normal for that champion and
role, not automatically bad.

### 3. Preserve direct-opponent magnitude

Early-lane features should be paired differences, normalized against historical
paired differences for the role:

- gold difference at 10 minutes;
- XP difference at 10 minutes;
- lane or jungle CS difference at 10 minutes;
- change in those differences from 10 to 15 minutes.

Using the 10→15 change avoids counting the 10-minute lead again inside the
15-minute value. When matchup samples are adequate, subtract the expected
champion-matchup and side advantage. Always create the opposite participant's
paired value by negation so the stored comparison is antisymmetric.

Support laning should use the bot duo's combined gold and XP difference, with a
small separate support roaming/vision component. Do not rate support CS.

### 4. Replace overlapping raw metrics with residual features

Use a compact feature set. Each feature should represent a distinct question.

#### Laning and economy

- historical-adjusted role-opponent lead at 10;
- historical-adjusted improvement or decline from 10 to 15;
- post-15 gold generation relative to champion/role expectation;
- jungle-only farm and enemy-jungle monster count for junglers;
- bot-duo economy for supports.

#### Combat contribution

- champion-damage output relative to expected output at the player's gold and
  game duration;
- kill participation residual relative to champion/role and team kill count;
- shutdown gold earned from recorded timeline events;
- ally healing/shielding and CC as champion/role residuals, not lobby ranks.

Predicting expected damage from gold prevents both raw damage and damage
efficiency from rewarding the same fact twice.

#### Objectives and map contribution

- explicit epic-monster killer/assistant credit;
- explicit building killer/assistant credit;
- objective and turret damage residuals by champion/role;
- objective steals as a sparse bonus with a strict cap.

Do not infer credit from frame proximity, and do not give all living teammates
credit for a team objective.

#### Vision

- vision score, control wards, and ward takedowns per minute as champion/role
  residuals;
- phase-aware rates so a long game does not let late totals overwhelm early
  contribution.

Ward placement location and ward quality remain unavailable and must not be
invented.

#### Discipline

- actual bounty and shutdown gold surrendered, time-weighted by death time;
- time spent dead, compared with the champion/role/duration expectation;
- repeated deaths with diminishing additional penalty, so the score does not
  count deaths, time dead, team death share, and surrendered bounty as four
  independent failures.

An optional objective-window indicator may state that a death occurred shortly
before an objective; it must not claim the death caused the objective loss.

### 5. Control team-state bias explicitly

Report two internal components:

- **Execution:** performance relative to expected output given resources and
  opportunities, such as damage conditional on gold.
- **Contribution:** advantages actually created or secured, such as role-opponent
  economy change, explicit objective credit, and bounty exchange.

Use both in the final score. Execution alone underrates players who created the
team lead; contribution alone mostly rates the winning team. Start at 55%
Execution and 45% Contribution, then fit the split during calibration.

Do not force a lobby mean of 5, equal team means, one high score per team, or a
fixed winner/loser gap. A dominant team may legitimately have several high
scores, and a close high-quality match may have many above-average players.

### 6. Fit weights rather than guessing them

Create a review set of at least 500 matches, stratified across:

- role and champion archetype;
- match duration and patch;
- close games and stomps;
- winners and losers;
- full and missing timelines.

Reviewers should make pairwise judgments, which are more reliable than inventing
an exact 0–10 score: who played better within each mirrored role, who was best on
each team, and which performances were clearly poor, average, good, or
exceptional. Store disagreement between reviewers.

Fit a regularized linear or monotonic model to those labels. Constrain signs for
features with an unambiguous direction, cap sparse bonuses, and use grouped
cross-validation by match so participants from one match never appear in both
training and validation. Keep a hand-authored fallback model until the learned
model beats it out of sample.

Win may be used as a weak validation signal, never as a scoring input or training
target. The model should correlate with winning without simply reproducing it.

### 7. Calibrate the displayed score and grades

Map the fitted latent score to a historical percentile within the queue, then to
the display scale. This makes the meaning stable:

| Rating | Historical interpretation |
|---:|---|
| 9.0–10.0 | exceptional, approximately top 2% |
| 8.0–8.9 | excellent, next 8% |
| 7.0–7.9 | good, next 15% |
| 6.0–6.9 | above average, next 25% |
| 4.0–5.9 | typical middle 35% |
| 3.0–3.9 | below average, next 10% |
| 0.0–2.9 | poor, bottom 5% |

The exact boundaries must come from held-out calibration data. Do not publish
letter grades until their percentile boundaries are measured. Keep MVP and ACE
as post-score labels only; they must not alter the number.

### 8. Treat missing data as uncertainty, not a new scale

Train or calibrate explicit Full and Limited variants to the same target scale.
For a missing timeline, predict timeline-dependent feature contributions from
available match features only when cross-validation shows that prediction helps;
otherwise use their conditional expectation of zero residual.

Return:

- score;
- model version;
- confidence level and estimated interval;
- baseline sample size;
- top positive and negative contributors;
- missing inputs that materially widened uncertainty.

A Limited 7.0 and a Full 7.0 should have the same expected meaning, while the
Limited result has a wider interval.

## Data and implementation layout

Suggested modules:

| File | Responsibility |
|---|---|
| `bot_app/rating_features.py` | Pure payload-to-feature extraction |
| `bot_app/rating_baselines.py` | Hierarchical robust baselines and shrinkage |
| `bot_app/rating_model.py` | Versioned scoring and confidence calculation |
| `bot_app/rating.py` | Stable public API and post-score MVP/ACE labels |
| `bot_app/rating_store.py` | Corpus/model SQLite persistence and migrations |
| `scripts/calibrate_ratings.py` | Offline fitting, validation, and report output |

The online bot should only extract features and load a previously validated
model artifact. It must never retrain during startup or polling.

Recommended model artifact metadata:

```text
schema_version
feature_version
model_version
queues
patch_window
trained_at
training_match_count
reviewed_match_count
baseline hierarchy and shrinkage constants
feature coefficients and caps
calibration mapping
validation metrics
```

## Validation gates

Do not replace version one until version two passes all of these on held-out
matches:

1. At least 70% agreement with reviewer judgments on mirrored-role pairings.
2. At least 65% agreement on reviewer-selected team best player.
3. No role's mean rating differs from another role's by more than 0.20.
4. No major champion archetype's mean differs by more than 0.30 after controlling
   for reviewer label.
5. Changing an unrelated player's raw statistic does not change this player's
   extracted score, except through a documented team-context feature.
6. A 1 CS role difference and an 80 CS role difference no longer normalize to
   the same magnitude.
7. Score distributions and percentile meanings remain stable across held-out
   patches; otherwise create a new patch calibration.
8. Full and Limited scores are tested against the same labels, with Limited
   confidence intervals showing their additional error.
9. Feature ablation demonstrates that each retained feature improves held-out
   accuracy; remove features that add only duplication.
10. Numeric score calculation remains independent of `win` and MVP/ACE labels.

Also retain deterministic contract tests for malformed roles, remakes, missing
challenge blocks, missing timelines, objective assistant credit, antisymmetric
opponent differences, model artifact versioning, and bounded output.

## Rollout plan

1. Instrument version one to save anonymized feature rows without changing the
   displayed rating.
2. Accumulate a representative corpus and build the reviewer tool.
3. Implement historical baselines and generate an offline disagreement report
   comparing version one, the fallback version-two model, and reviewer labels.
4. Fit and cross-validate weights and calibration.
5. Run version two in shadow mode. Log large version-one/version-two differences
   with feature explanations, but continue displaying version one.
6. Review at least two patch cycles for role, champion, side, duration, and
   win/loss bias.
7. Switch the display only after the validation gates pass. Preserve the old
   model artifact so rollback is immediate.

## Scope and honesty

Match-V5 and timeline data still cannot reliably observe ward quality, wave
state, cooldown usage, missed skillshots, peel quality, communication, matchup
execution, split-push pressure, or why a player chose an action. Version two can
be materially fairer and more stable, but it cannot be an objective measurement
of player skill. The UI should call it a performance estimate and expose its
confidence rather than presenting it as ground truth.
