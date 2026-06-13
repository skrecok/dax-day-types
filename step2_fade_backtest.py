"""
STEP 2 — unconditional reactive FADE expectancy (the definitive test)
=====================================================================
We are NOT predicting the day type (step 1 proved we can't).
We test whether a pure reactive mean-reversion TRIGGER has positive net
expectancy on its own, accepting that it WILL get run over on trend days.

Trigger (classic VWAP standard-deviation bands, all causal / developing):
  - developing VWAP_t and volume-weighted sigma_t computed bar-by-bar
    from session open up to t (tick-volume weighted, the data we have).
  - if price closes >= VWAP + k*sigma  -> FADE SHORT (bet on reversion)
    if price closes <= VWAP - k*sigma  -> FADE LONG
  - target = VWAP at entry (return to value)
  - stop   = one more sigma beyond entry band  (k_stop = k+1)
  - forced exit at cash close; round-trip cost subtracted.
  - ONE trade/day (first trigger). Params pre-registered, NOT tuned.

Payoff is intentionally asymmetric (~+2 sigma target vs ~-1 sigma stop):
fade systems show high win-rate + fat left tail, so we report the WHOLE
R distribution and the tail, not just the mean. R = risk (stop distance).
"""
import numpy as np, pandas as pd

CSV = "/mnt/user-data/uploads/1780852998140_deuidxeur-v-m1-bid-2015-01-01-2025-06-01.csv"
RTH_A, RTH_B = 9*60, 17*60+30
SIG_START, SIG_LAST = 10*60+30, 16*60      # when entries are allowed (Berlin)
WARMUP = 60                                 # bars before sigma is trusted
K, K_STOP = 2.0, 3.0                        # band sigma, stop sigma
RT_COST_PTS = 1.0                           # round-trip cost (points)
TRAIN_END = 2022


def load():
    df = pd.read_csv(CSV)
    dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["dt"], df["date"] = dt, dt.dt.date
    df["tod"] = dt.dt.hour*60 + dt.dt.minute
    return df[(df.tod >= RTH_A) & (df.tod <= RTH_B)].copy()


def simulate_day(g):
    g = g.sort_values("dt")
    n = len(g)
    if n < 300:
        return None
    o   = g.open.values; h = g.high.values; l = g.low.values; c = g.close.values
    vol = np.maximum(g.volume.values, 1e-12)
    tod = g.tod.values
    tp  = (h + l + c) / 3.0
    # developing VWAP and volume-weighted sigma (running, O(n))
    cv  = np.cumsum(vol)
    cpv = np.cumsum(vol * tp)
    cp2 = np.cumsum(vol * tp * tp)
    vwap = cpv / cv
    var  = cp2 / cv - vwap**2
    sig  = np.sqrt(np.clip(var, 0, None))

    # full-day directionality for postfactum diagnostic label
    rng = h.max() - l.min()
    D   = abs(c[-1] - o[0]) / rng if rng > 0 else 0.0

    for i in range(n):
        if i < WARMUP or not (SIG_START <= tod[i] <= SIG_LAST):
            continue
        s = sig[i]
        if s <= 0:
            continue
        up, dn = vwap[i] + K*s, vwap[i] - K*s
        side = 0
        if c[i] >= up:   side = -1            # too high -> fade short
        elif c[i] <= dn: side = +1            # too low  -> fade long
        if side == 0:
            continue
        entry  = c[i]
        target = vwap[i]                       # back to value
        stop   = entry - side * s              # one more sigma against us
        risk   = abs(entry - stop)             # ~1 sigma (the R unit)
        if risk <= 0:
            continue
        # walk forward to target / stop / close
        exitp = c[-1]                          # default: forced close
        for j in range(i+1, n):
            if side == -1:                     # short: profit down, stop up
                if h[j] >= stop:  exitp = stop;   break
                if l[j] <= target: exitp = target; break
            else:                              # long: profit up, stop down
                if l[j] <= stop:  exitp = stop;   break
                if h[j] >= target: exitp = target; break
        pnl_pts = side * (exitp - entry) - RT_COST_PTS
        return dict(date=g.date.iloc[0], side=side, risk=risk,
                    pnl_pts=pnl_pts, R=pnl_pts/risk, D=D, entry=entry)
    return None  # no trigger that day


def stats(df, lbl):
    if len(df) == 0:
        print(f"  {lbl}: no trades"); return
    R = df["R"].values
    n = len(R); m = R.mean(); sd = R.std()
    t = m/(sd/np.sqrt(n)) if sd > 0 else 0
    wr = (R > 0).mean()
    pcts = np.percentile(R, [1,5,25,50,75,95,99])
    worse1 = (R < -1).mean()
    print(f"  {lbl}: n={n}  E[R]={m:+.3f} (t={t:+.2f})  win%={wr*100:.1f}  "
          f"sumR={R.sum():+.1f}")
    print(f"      R pctiles 1/5/25/50/75/95/99: "
          f"{pcts[0]:+.2f}/{pcts[1]:+.2f}/{pcts[2]:+.2f}/{pcts[3]:+.2f}/"
          f"{pcts[4]:+.2f}/{pcts[5]:+.2f}/{pcts[6]:+.2f}   "
          f"trades worse than -1R: {worse1*100:.1f}%")


def main():
    df = load()
    trades = []
    for _, g in df.groupby("date", sort=True):
        r = simulate_day(g)
        if r: trades.append(r)
    t = pd.DataFrame(trades)
    t["yr"] = pd.to_datetime(t["date"]).dt.year
    print(f"trigger fired on {len(t)} of ~2657 days "
          f"({len(t)/2657*100:.0f}% of days)\n")
    print(f"cost {RT_COST_PTS} pt round-trip | band {K}sigma | stop {K_STOP}sigma")
    stats(t, "ALL          ")
    stats(t[t.yr <= TRAIN_END], "in-sample 15-22")
    stats(t[t.yr >= 2023],      "out-sample 23-24")

    # gross (no cost) for reference
    tg = t.copy(); tg["R"] = (t["pnl_pts"] + RT_COST_PTS)/t["risk"]
    stats(tg, "ALL  (GROSS)  ")

    # postfactum diagnostic: does fade die on trend days as theory says?
    print("\ndiagnostic — net E[R] by full-day directionality (postfactum):")
    t["Dbin"] = pd.cut(t["D"], [0,.35,.6,1.01],
                       labels=["rotational","slow_trend","fast_trend"])
    print(t.groupby("Dbin", observed=True)["R"]
            .agg(n="size", ER="mean", win=lambda s:(s>0).mean()).round(3).to_string())

    # cost sensitivity (net E[R])
    print("\ncost sensitivity, net E[R] (all):")
    for ct in (0.0, 0.5, 1.0, 2.0):
        R = (t["pnl_pts"] + RT_COST_PTS - ct)/t["risk"]
        print(f"   {ct:>4} pt -> {R.mean():+.3f}")

    t.to_csv("/mnt/user-data/outputs/dax_step2_fade_trades.csv", index=False)
    print("\nsaved -> dax_step2_fade_trades.csv")


if __name__ == "__main__":
    main()
