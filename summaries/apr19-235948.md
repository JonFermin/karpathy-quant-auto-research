# quant-research/apr19-235948

- **UNIVERSE_TAG**: sp100_2024 (default)
- **Baseline (seed)**: baseline: 0.915293  (commit 00079a7)
- **Running best**:   running_best: 0.915293  (commit 00079a7)
- **Trials logged**:  20
- **Stop reason**:    trial-cap (20/20); no kept trial beat baseline under strict-honesty bootstrap-CI hurdle

## results.tsv

```
commit	oos_sharpe	max_dd	turnover	status	description
00079a7	0.915293	0.3181	6.42	keep	thesis: baseline anchor — 12-1 momentum top decile, monthly rebalance; strategy-neutral refactor
f119d01	0.777476	0.3101	6.94	discard	thesis: risk-adjusted momentum — scale 12-1 return by 252d daily-return vol to de-emphasize noisy high-vol winners
238daee	0.717152	0.3132	5.67	discard	thesis: widen to top quintile — 20 names on SP100 diversifies vs 10-name concentration
145d291	0.894025	0.3019	8.07	discard	thesis: inverse-vol sizing inside top decile — 63d daily-return vol; equal risk-contribution vs equal dollar
686e903	0.888909	0.3219	6.20	discard	thesis: residual 12-1 momentum — compound daily (rets - equal-weight mkt) to rank; Grundy-Martin crash mitigation
4d3e87f	0.887734	0.2359	8.02	discard	thesis: regime gate — halve gross exposure when EW-universe dd > 15% (Daniel-Moskowitz crash avoidance)
34d7872	0.719343	0.3240	13.93	discard	thesis: exclude names in top-quartile of last-5d return — avoid extended breakouts (Jegadeesh 1990 reversal)
6e16bde	-0.038893	0.2562	6.32	discard	thesis: long-short 12-1 momentum — top decile long 50pct + bottom decile short 50pct, classic Fama-French MOM
11a7408	0.938553	0.3005	8.75	discard	thesis: 6-1 momentum vs 12-1 — 126d lookback tracks recent regime faster; reversal-clean
afd36b7	0.740666	0.3284	9.41	discard	thesis: blend 12-1 and 6-1 momentum ranks — cross-horizon averaging reduces single-lookback noise
971b469	0.930015	0.3324	7.50	discard	thesis: require price>200d MA on top of 12-1 momentum decile — Faber trend filter drops rolling-over names
ce93a5c	0.869037	0.3272	4.12	discard	thesis: bi-monthly rebalance — halves turnover, momentum decay slow enough to tolerate
270a409	0.915293	0.3181	6.42	discard	thesis: absolute-momentum gate — only hold a name if its own 12-1 return > 0; crash protection overlay on cross-section
a9f1a6c	1.068254	0.3600	6.60	discard	thesis: concentrate to top 5 percent (5 names) — if momentum premium is tail-heavy, fewer/stronger names harvests more per position
a3202f5	0.896722	0.3181	6.52	discard	thesis: rank-stability — top decile AND in top-half 21d ago; Novy-Marx sustained-winner premium
9a87bb1	0.672437	0.2935	11.95	discard	thesis: momentum + low-vol quality — lower-vol half of top-momentum decile (QMJ composite)
f694ff7	0.556384	0.3426	12.43	discard	thesis: multi-horizon confirmation — top quartile of both 12-1 and 3-1; cross-horizon agreement weeds out spurious signals
7d3a31f	0.801848	0.1788	9.12	discard	thesis: vol-targeted sizing to 12 pct annualized — Moreira-Muir 2017 vol-managed portfolios; scale inverse to 20d realized
7f6f74d	0.882477	0.3208	3.47	discard	thesis: overlapping portfolios — equal-weight average of last-3-months top-decile sleeves; canonical JT 1993 construction
4593958	0.871776	0.2819	5.70	discard	thesis: monthly vol-target to 15 pct — Moreira-Muir with month-end cadence to avoid daily churn of trial 18
```

## Theses by status

### keep
- thesis: baseline anchor — 12-1 momentum top decile, monthly rebalance; strategy-neutral refactor

### discard
- thesis: risk-adjusted momentum — scale 12-1 return by 252d daily-return vol to de-emphasize noisy high-vol winners
- thesis: widen to top quintile — 20 names on SP100 diversifies vs 10-name concentration
- thesis: inverse-vol sizing inside top decile — 63d daily-return vol; equal risk-contribution vs equal dollar
- thesis: residual 12-1 momentum — compound daily (rets - equal-weight mkt) to rank; Grundy-Martin crash mitigation
- thesis: regime gate — halve gross exposure when EW-universe dd > 15% (Daniel-Moskowitz crash avoidance)
- thesis: exclude names in top-quartile of last-5d return — avoid extended breakouts (Jegadeesh 1990 reversal)
- thesis: long-short 12-1 momentum — top decile long 50pct + bottom decile short 50pct, classic Fama-French MOM
- thesis: 6-1 momentum vs 12-1 — 126d lookback tracks recent regime faster; reversal-clean
- thesis: blend 12-1 and 6-1 momentum ranks — cross-horizon averaging reduces single-lookback noise
- thesis: require price>200d MA on top of 12-1 momentum decile — Faber trend filter drops rolling-over names
- thesis: bi-monthly rebalance — halves turnover, momentum decay slow enough to tolerate
- thesis: absolute-momentum gate — only hold a name if its own 12-1 return > 0; crash protection overlay on cross-section
- thesis: concentrate to top 5 percent (5 names) — if momentum premium is tail-heavy, fewer/stronger names harvests more per position
- thesis: rank-stability — top decile AND in top-half 21d ago; Novy-Marx sustained-winner premium
- thesis: momentum + low-vol quality — lower-vol half of top-momentum decile (QMJ composite)
- thesis: multi-horizon confirmation — top quartile of both 12-1 and 3-1; cross-horizon agreement weeds out spurious signals
- thesis: vol-targeted sizing to 12 pct annualized — Moreira-Muir 2017 vol-managed portfolios; scale inverse to 20d realized
- thesis: overlapping portfolios — equal-weight average of last-3-months top-decile sleeves; canonical JT 1993 construction
- thesis: monthly vol-target to 15 pct — Moreira-Muir with month-end cadence to avoid daily churn of trial 18

### crash
