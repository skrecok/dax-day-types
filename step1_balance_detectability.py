"""
STEP 1 — causal detectability of the BALANCE state
===================================================
Question: at a mid-session decision time T, using ONLY information up to T,
can we predict whether the REST of the day (T -> close) will be
mean-reverting (balance) rather than expanding (trend)?

Discipline that makes this honest:
  - features are computed strictly on [09:00, T]   (the past)
  - the LABEL is computed strictly on [T, close]   (the future)
    -> no look-ahead. This is the exact fix for the oracle illusion
       that inflated fast_trend earlier.
  - train 2015-2022, test 2023-2024 (touch test once).
  - SAME features, two targets (balance vs trend) so detectability is
    compared apples-to-apples against the AUC~0.64 trend benchmark.

If balance AUC ~ 0.64  -> fade is almost certainly dead, learned in 1 run.
If balance AUC notably > 0.64 -> there is something causal to build on.
No volume profile needed yet: we proxy "value" with developing VWAP
(weighted by the tick volume we already have).
"""
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score

CSV = "/mnt/user-data/uploads/1780852998140_deuidxeur-v-m1-bid-2015-01-01-2025-06-01.csv"
RTH_A, IB_END, T_DEC, RTH_B = (9*60), (10*60), (12*60), (17*60+30)   # Berlin minutes
MIN_PAST, MIN_FUT = 120, 120
BAL_CUT, TREND_CUT = 0.35, 0.60          # future directionality cutoffs
TRAIN_END = 2022                          # <=2022 train, >=2023 test


def load():
    df = pd.read_csv(CSV)
    dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["dt"], df["date"] = dt, dt.dt.date
    df["tod"] = dt.dt.hour*60 + dt.dt.minute
    return df[(df.tod >= RTH_A) & (df.tod <= RTH_B)].copy()


def day_row(g, d):
    g = g.sort_values("dt")
    past = g[g.tod < T_DEC]
    fut  = g[g.tod >= T_DEC]
    if len(past) < MIN_PAST or len(fut) < MIN_FUT:
        return None
    ib = g[g.tod < IB_END]
    if len(ib) < 30:
        return None

    o   = past.open.iloc[0]
    pT  = past.close.iloc[-1]                       # price at decision time
    hP, lP = past.high.max(), past.low.min()        # morning range
    rngP = hP - lP
    if rngP <= 0: return None
    ibh, ibl = ib.high.max(), ib.low.min()
    ib_rng = ibh - ibl

    # developing VWAP up to T (tick-volume weighted)
    vol = past.volume.values
    tp  = (past.high.values + past.low.values + past.close.values)/3
    vwapT = np.average(tp, weights=vol) if vol.sum() > 0 else tp.mean()
    # VWAP-cross frequency in the morning = rotation tendency
    side = np.sign(past.close.values - vwapT)
    crosses = int(np.sum(np.abs(np.diff(side)) > 0))

    # post-IB containment: share of post-IB morning bars staying inside IB
    pib = past[past.tod >= IB_END]
    inside_ib = float(np.mean((pib.high <= ibh) & (pib.low >= ibl))) if len(pib) else 1.0
    # extension beyond IB (expansion so far)
    ext = max(0.0, hP - ibh) + max(0.0, ibl - lP)

    morn_path = past.close.diff().abs().sum()
    morn_er   = abs(pT - o)/morn_path if morn_path > 0 else 0.0
    morn_ret  = np.diff(np.log(past.close.values))
    feat = dict(
        date=d,
        morn_dir   = (pT - o)/rngP,                 # directionality so far
        pos_in_rng = (pT - lP)/rngP,                # where we sit in morning range
        morn_er    = morn_er,                       # choppy morning -> low
        ib_rng_adr = ib_rng,                        # /ADR added later
        morn_rng   = rngP,                          # /ADR added later
        ext_adr    = ext,                           # /ADR added later
        inside_ib  = inside_ib,                      # containment
        cross_rate = crosses/(len(past)/60.0),       # vwap crosses per hour
        dist_vwap  = abs(pT - vwapT),               # /ADR added later
        gap        = o - g.iloc[0].open,            # (set below properly)
        morn_vol   = morn_ret.std() if len(morn_ret) else 0.0,
        # ---- future-only label material ----
        pT_=pT,
        c_fut=fut.close.iloc[-1], h_fut=fut.high.max(), l_fut=fut.low.min(),
        full_rng=g.high.max()-g.low.min(),
    )
    return feat


def build():
    df = load()
    rows = []
    for d, g in df.groupby("date", sort=True):
        r = day_row(g, d)
        if r: rows.append(r)
    f = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)

    # ADR20 (trailing, causal) from full RTH range
    f["adr"] = f["full_rng"].rolling(20, min_periods=5).median().shift(1)
    f = f.dropna(subset=["adr"]).copy()
    for c in ["ib_rng_adr","morn_rng","ext_adr","dist_vwap"]:
        f[c] = f[c]/f["adr"]
    # overnight gap proxy: prev close to today open
    f["prev_close"] = f["c_fut"].shift(1)
    f["gap"] = (f["pT_"] - f["pT_"].shift(1))/f["adr"]   # crude regime drift proxy
    f["prev_dir"] = f["morn_dir"].shift(1)

    # ---- future-only directionality of the REST of the day ----
    fut_rng = (f["h_fut"] - f["l_fut"]).replace(0, np.nan)
    f["D_fut"] = (f["c_fut"] - f["pT_"]).abs()/fut_rng
    f = f.dropna(subset=["D_fut","gap","prev_dir"]).copy()
    f["y_bal"]   = (f["D_fut"] <  BAL_CUT ).astype(int)
    f["y_trend"] = (f["D_fut"] >= TREND_CUT).astype(int)
    return f


FEATS = ["morn_dir","pos_in_rng","morn_er","ib_rng_adr","morn_rng","ext_adr",
         "inside_ib","cross_rate","dist_vwap","gap","morn_vol","prev_dir"]


def evaluate(f, target):
    yr = pd.to_datetime(f["date"]).dt.year
    tr, te = f[yr <= TRAIN_END], f[yr >= 2023]
    Xtr, Xte = tr[FEATS].values, te[FEATS].values
    ytr, yte = tr[target].values, te[target].values
    base = yte.mean()

    sc = StandardScaler().fit(Xtr)
    lr = LogisticRegression(max_iter=2000, C=1.0).fit(sc.transform(Xtr), ytr)
    p_lr = lr.predict_proba(sc.transform(Xte))[:,1]
    gb = HistGradientBoostingClassifier(max_depth=3, max_iter=300,
                                        learning_rate=0.05,
                                        l2_regularization=1.0).fit(Xtr, ytr)
    p_gb = gb.predict_proba(Xte)[:,1]

    def line(name, p):
        auc = roc_auc_score(yte, p); ap = average_precision_score(yte, p)
        k = int(len(p)*0.20); idx = np.argsort(p)[-k:]
        prec = yte[idx].mean()
        print(f"    {name:<8} AUC={auc:.3f}  AP={ap:.3f}  "
              f"prec@top20%={prec:.3f}")
    print(f"  target={target}   train n={len(tr)}  test n={len(te)}  "
          f"base rate(test)={base:.3f}")
    line("logreg", p_lr); line("histGB", p_gb)

    # logreg standardized coefficients (which causal signals matter)
    coef = pd.Series(lr.coef_[0], index=FEATS).sort_values(key=abs, ascending=False)
    print("    top logreg coefs:", ", ".join(f"{k}{v:+.2f}" for k,v in coef.head(5).items()))
    return None


def main():
    f = build()
    print(f"days usable: {len(f)}  ({f.date.min()} -> {f.date.max()})")
    print(f"decision T = 12:00 Berlin | balance cut D_fut<{BAL_CUT} | trend cut D_fut>={TREND_CUT}\n")
    print(">>> BALANCE detectability (can we see 'rest of day rotates'?)")
    evaluate(f, "y_bal")
    print("\n>>> TREND detectability (benchmark — should land near the old ~0.64)")
    evaluate(f, "y_trend")
    f.to_csv("/mnt/user-data/outputs/dax_step1_causal_features.csv", index=False)
    print("\nsaved -> dax_step1_causal_features.csv")


if __name__ == "__main__":
    main()
