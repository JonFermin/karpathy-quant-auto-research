# quant-research/0420-115857

- **UNIVERSE_TAG**: sp400_2024
- **Baseline (seed)**: baseline: 0.960896  (commit c812de6)
- **Running best**:   running_best: 0.960896  (commit c812de6)
- **Trials logged**:  20
- **Stop reason**:    trial-cap

## results.tsv

```
commit	oos_sharpe	max_dd	turnover	status	description
c812de6	0.960896	0.4356	6.07	keep	thesis: baseline anchor — 12-1 momentum, top decile, EW, monthly rebal (AST-only rewrite)
9dafede	0.859631	0.4406	6.71	discard	thesis: risk-adjusted momentum (mom/vol) emphasizes high-Sharpe winners, should reduce noise in mid-cap dispersion
aeb0e2f	0.828480	0.4228	7.50	discard	thesis: inverse-vol sizing within top decile — steadier portfolio vol, tempers high-vol noise
0453415	0.833633	0.4329	5.38	discard	thesis: top quintile instead of decile — wider breadth reduces idiosyncratic variance on SP400 mid-caps
2ec51a6	0.993863	0.4194	3.45	discard	thesis: quarterly rebalance cuts turnover; momentum persists past 1 month in diversified baskets
03dacc5	0.835573	0.4892	9.31	discard	thesis: 6-1 momentum — mid-caps exhibit faster momentum decay, shorter lookback captures fresher trends
f0be565	0.795253	0.4809	9.94	discard	thesis: blended 3-1/6-1/12-1 momentum — horizon-robust, picks names with consistent trend
81ef267	0.777982	0.3205	11.45	discard	thesis: regime gate — flat when universe below 200d MA — momentum crashes at bear reversals, trend filter cuts tail risk
a58f14c	0.667503	0.4731	12.76	discard	thesis: 5d reversal filter on top-decile momentum — drop recently overbought names prone to short-term mean reversion
8d5246f	-0.460088	3.4757	6.17	discard	thesis: market-neutral L/S momentum — strip beta, earn pure cross-sectional premium
777d5dc	0.344662	0.4144	2.50	discard	thesis: low-volatility factor — lowest-vol decile, tap the low-vol anomaly with smaller DD than momentum
92e2445	0.550753	0.4110	6.98	discard	thesis: top-quintile momentum, tilt to low-vol names within selection — marry two anomalies
93257f9	0.719791	0.4965	9.85	discard	thesis: Sharpe-based 6-1 momentum (6m ret / 6m vol) — prefer steady climbers over lottery spikes on mid-caps
160305b	1.002169	0.4686	9.03	discard	thesis: absolute trend filter (price>200d MA) + top-decile 12-1 momentum — avoid best-of-bad in falling markets
5098881	1.269198	0.4257	7.14	discard	thesis: top ventile (5%) — sharper exposure to momentum tail, ~20 names still diversified
5a67c5a	0.896268	0.4337	11.55	discard	thesis: cross-horizon intersection — top quartile in both 3-1 and 12-1 demands trend agreement across scales
bcaa07a	1.085139	0.3934	6.13	discard	thesis: 12-0 momentum (no skip) — 1-month reversal may be weaker on mid-caps, use most recent month
9068c57	0.946727	0.4342	7.14	discard	thesis: healthy-momentum filter — drop wounded leaders >30% off 252d high, keep climbing leaders only
86deb87	0.644148	0.4326	4.66	discard	thesis: equal-weight all positive-mom names — broad mid-cap trend exposure, trade concentration for diversification
735c137	0.977265	0.4570	6.07	discard	thesis: 12-2 momentum — wider 2-month recency skip strips more short-horizon reversal noise
```

## Theses by status

### keep
- thesis: baseline anchor — 12-1 momentum, top decile, EW, monthly rebal (AST-only rewrite)

### discard
- thesis: risk-adjusted momentum (mom/vol) emphasizes high-Sharpe winners, should reduce noise in mid-cap dispersion
- thesis: inverse-vol sizing within top decile — steadier portfolio vol, tempers high-vol noise
- thesis: top quintile instead of decile — wider breadth reduces idiosyncratic variance on SP400 mid-caps
- thesis: quarterly rebalance cuts turnover; momentum persists past 1 month in diversified baskets
- thesis: 6-1 momentum — mid-caps exhibit faster momentum decay, shorter lookback captures fresher trends
- thesis: blended 3-1/6-1/12-1 momentum — horizon-robust, picks names with consistent trend
- thesis: regime gate — flat when universe below 200d MA — momentum crashes at bear reversals, trend filter cuts tail risk
- thesis: 5d reversal filter on top-decile momentum — drop recently overbought names prone to short-term mean reversion
- thesis: market-neutral L/S momentum — strip beta, earn pure cross-sectional premium
- thesis: low-volatility factor — lowest-vol decile, tap the low-vol anomaly with smaller DD than momentum
- thesis: top-quintile momentum, tilt to low-vol names within selection — marry two anomalies
- thesis: Sharpe-based 6-1 momentum (6m ret / 6m vol) — prefer steady climbers over lottery spikes on mid-caps
- thesis: absolute trend filter (price>200d MA) + top-decile 12-1 momentum — avoid best-of-bad in falling markets
- thesis: top ventile (5%) — sharper exposure to momentum tail, ~20 names still diversified
- thesis: cross-horizon intersection — top quartile in both 3-1 and 12-1 demands trend agreement across scales
- thesis: 12-0 momentum (no skip) — 1-month reversal may be weaker on mid-caps, use most recent month
- thesis: healthy-momentum filter — drop wounded leaders >30% off 252d high, keep climbing leaders only
- thesis: equal-weight all positive-mom names — broad mid-cap trend exposure, trade concentration for diversification
- thesis: 12-2 momentum — wider 2-month recency skip strips more short-horizon reversal noise

### crash
