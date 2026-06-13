"""
DAX trend-expectancy research pipeline  —  v0.1
================================================

Idea (continuation of the deleted research):
  - A "day type" (Dalton) is not a cluster but a point on a continuous
    spectrum. So we SCORE trendiness per day instead of clustering.
  - Then we ask the only question that matters for an edge:
    does an early, *tradeable* signal carry positive expectancy
    (matozhidanie) AFTER costs?  The working hypothesis (efficiency /
    smoothing) is that gross edge exists but net edge ~ 0.

Two clearly separated layers:
  (A) DESCRIPTIVE  — uses the whole day. Characterizes the spectrum.
                     NOT a strategy (it peeks at the future).
  (B) TRADEABLE    — signal known by 10:00 Berlin (after the 1st hour),
                     exit at cash close. Everything here is honest:
                     no look-ahead, costs applied, CIs + t-stats.

Units: data is DAX index points (Dukascopy CFD, tick-volume proxy).
Full FDAX contract = EUR 25 / index point (verify exact specs separately).
Costs are expressed in INDEX POINTS so the analysis stays scale-free.
"""

import numpy as np
import pandas as pd

CSV = "/mnt/user-data/uploads/1780852998140_deuidxeur-v-m1-bid-2015-01-01-2025-06-01.csv"

# ---- knobs -----------------------------------------------------------------
RTH_START   = (9, 0)     # Xetra cash open  (Europe/Berlin)
ENTRY_TIME  = (10, 0)    # decision/entry after the first hour
RTH_END     = (17, 30)   # Xetra cash close
MIN_BARS    = 300        # drop holidays / half-days (RTH has ~510 min)
RT_COST_PTS = 1.0        # round-trip cost in index points (spread+comm), a GUESS
# ----------------------------------------------------------------------------


def load():
    df = pd.read_csv(CSV)
    dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df = df.assign(
        dt=dt,
        date=dt.dt.date,
        tod=dt.dt.hour * 60 + dt.dt.minute,   # minutes since midnight, Berlin
    )
    return df


def rth_slice(df):
    a = RTH_START[0] * 60 + RTH_START[1]
    b = RTH_END[0] * 60 + RTH_END[1]
    return df[(df["tod"] >= a) & (df["tod"] <= b)].copy()


def per_day_features(rth):
    """One row per trading day. Mix of full-day (descriptive) and
    first-hour (tradeable) features."""
    ent = ENTRY_TIME[0] * 60 + ENTRY_TIME[1]
    out = []
    for d, g in rth.groupby("date", sort=True):
        g = g.sort_values("dt")
        if len(g) < MIN_BARS:
            continue
        o   = g["open"].iloc[0]
        c   = g["close"].iloc[-1]
        hi  = g["high"].max()
        lo  = g["low"].min()
        rng = hi - lo
        if rng <= 0:
            continue
        net = c - o
        # efficiency ratio over the full RTH (Kaufman): |net| / path length
        path = g["close"].diff().abs().sum()
        er   = abs(net) / path if path > 0 else 0.0
        # close location value in [-1, 1]; sign=direction, |.|~1 => trend close
        clv  = (c - (hi + lo) / 2) / (rng / 2)

        # ---- first hour (known by ENTRY_TIME) ----
        fh = g[g["tod"] < ent]
        post = g[g["tod"] >= ent]
        if len(fh) < 20 or len(post) < 20:
            continue
        fh_o    = fh["open"].iloc[0]
        entry_p = fh["close"].iloc[-1]          # ~price at 10:00
        fh_ret  = entry_p - fh_o
        fh_path = fh["close"].diff().abs().sum()
        fh_er   = abs(fh_ret) / fh_path if fh_path > 0 else 0.0
        fh_rng  = fh["high"].max() - fh["low"].min()

        out.append(dict(
            date=d, o=o, c=c, hi=hi, lo=lo, rng=rng, net=net,
            ret_pct=net / o, er=er, clv=clv,
            net_over_rng=net / rng,
            entry_p=entry_p, fh_ret=fh_ret, fh_er=fh_er, fh_rng=fh_rng,
            post_close=c,
        ))
    f = pd.DataFrame(out).sort_values("date").reset_index(drop=True)
    # trailing 20-day median RTH range -> normalize trades into "R"
    f["atr"] = f["rng"].rolling(20, min_periods=5).median().shift(1)
    return f


def describe_spectrum(f):
    print("\n=== (A) DESCRIPTIVE: the trendiness spectrum ===")
    print(f"days analysed: {len(f)}   ({f.date.min()} -> {f.date.max()})")
    tr = f["er"]
    print(f"efficiency-ratio  mean={tr.mean():.3f}  median={tr.median():.3f}  "
          f"std={tr.std():.3f}")
    # is the distribution continuous/unimodal (=> score, don't cluster)?
    try:
        from diptest import diptest
        dip, p = diptest(tr.values)
        verdict = "unimodal (no clean clusters -> scoring justified)" if p > 0.05 \
                  else "multimodal (clusters may exist)"
        print(f"Hartigan dip test:  dip={dip:.4f}  p={p:.3f}  -> {verdict}")
    except Exception:
        print("Hartigan dip test: (diptest not installed; skipping)")
    # decile table: does a strong trend-close coincide with a directional day?
    f = f.copy()
    f["er_dec"] = pd.qcut(f["er"], 10, labels=False, duplicates="drop") + 1
    tab = f.groupby("er_dec").agg(
        n=("er", "size"),
        er_mean=("er", "mean"),
        abs_net_over_rng=("net_over_rng", lambda s: s.abs().mean()),
        abs_clv=("clv", lambda s: s.abs().mean()),
        ret_pct_std=("ret_pct", "std"),
    ).round(3)
    print("\nby efficiency-ratio decile (1=balance ... 10=pure trend):")
    print(tab.to_string())


def expectancy(f):
    print("\n=== (B) TRADEABLE: first-hour continuation, exit at cash close ===")
    g = f.dropna(subset=["atr"]).copy()
    side = np.sign(g["fh_ret"]).replace(0, np.nan)
    g = g[side.notna()].copy()
    side = side.loc[g.index]

    gross = side * (g["post_close"] - g["entry_p"])     # points, no cost
    net   = gross - RT_COST_PTS                          # after round-trip cost
    g["gross"], g["net"] = gross, net
    g["net_R"] = net / g["atr"]                           # normalized across decade
    g["net_pct"] = net / g["entry_p"] * 100

    def stats(x, lbl):
        x = x.dropna()
        m, s, n = x.mean(), x.std(), len(x)
        t = m / (s / np.sqrt(n)) if s > 0 else 0
        wr = (x > 0).mean()
        print(f"  {lbl:<26} n={n:5d}  mean={m:+.3f}  win%={wr*100:4.1f}  "
              f"t={t:+.2f}")
    print(f"round-trip cost assumed: {RT_COST_PTS} pts")
    stats(g["gross"], "expectancy GROSS (pts)")
    stats(g["net"],   "expectancy NET   (pts)")
    stats(g["net_R"], "expectancy NET   (R)")

    # tradeable conditioning: stronger first-hour trend -> more continuation?
    g["fh_dec"] = pd.qcut(g["fh_er"], 5, labels=False, duplicates="drop") + 1
    print("\n  net expectancy by first-hour efficiency quintile "
          "(1=choppy open ... 5=clean early trend):")
    q = g.groupby("fh_dec").agg(
        n=("net", "size"),
        net_pts=("net", "mean"),
        net_R=("net_R", "mean"),
        win=("net", lambda s: (s > 0).mean()),
    ).round(3)
    print(q.to_string())

    # cost sensitivity
    print("\n  cost sensitivity (mean net pts at different round-trip costs):")
    for ct in (0.0, 0.5, 1.0, 2.0, 4.0):
        print(f"    {ct:>4} pts -> {(g['gross'] - ct).mean():+.3f}")
    return g


def main():
    df = load()
    rth = rth_slice(df)
    f = per_day_features(rth)
    describe_spectrum(f)
    g = expectancy(f)
    f.to_csv("/mnt/user-data/outputs/dax_daily_features.csv", index=False)
    print("\nsaved per-day feature table -> dax_daily_features.csv")
    print("\nCAVEATS: one instrument, one setup, in-sample (no walk-forward "
          "split yet); costs are a guess; CFD tick-volume proxy, not FDAX; "
          "many untested setups => multiple-testing risk.")


if __name__ == "__main__":
    main()
