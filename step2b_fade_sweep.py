"""
STEP 2b — fade TARGET-GEOMETRY sweep (robustness)
=================================================
Same entry (price >= VWAP + 2 sigma), same stop (1 sigma => risk unit R),
only the PROFIT TARGET varies:
   t_mult=0.5 -> tiny scalp  (+0.5R win, high win-rate, fat left tail)
   t_mult=1.0 -> symmetric   (+1.0R)
   t_mult=1.5 ->             (+1.5R)
   t_mult=2.0 -> full return to VWAP (+2.0R, the step-2 version)

If E[R] is ~0 gross / negative net across the WHOLE sweep, the fade null
cannot be blamed on one unlucky parametrization. Entries identical across
variants, so this isolates target choice cleanly.
"""
import numpy as np, pandas as pd

CSV = "/mnt/user-data/uploads/1780852998140_deuidxeur-v-m1-bid-2015-01-01-2025-06-01.csv"
RTH_A, RTH_B = 9*60, 17*60+30
SIG_START, SIG_LAST = 10*60+30, 16*60
WARMUP, K, K_STOP, RT_COST_PTS, TRAIN_END = 60, 2.0, 3.0, 1.0, 2022
TARGETS = [0.5, 1.0, 1.5, 2.0]


def load():
    df = pd.read_csv(CSV)
    dt = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert("Europe/Berlin")
    df["dt"], df["date"] = dt, dt.dt.date
    df["tod"] = dt.dt.hour*60 + dt.dt.minute
    return df[(df.tod >= RTH_A) & (df.tod <= RTH_B)].copy()


def simulate_day(g):
    """Returns the (one) entry for the day + the exit price for EACH target."""
    g = g.sort_values("dt"); n = len(g)
    if n < 300: return None
    o,h,l,c = g.open.values,g.high.values,g.low.values,g.close.values
    vol = np.maximum(g.volume.values,1e-12); tod = g.tod.values
    tp = (h+l+c)/3.0
    cv,cpv,cp2 = np.cumsum(vol),np.cumsum(vol*tp),np.cumsum(vol*tp*tp)
    vwap = cpv/cv; sig = np.sqrt(np.clip(cp2/cv - vwap**2,0,None))
    rng = h.max()-l.min(); D = abs(c[-1]-o[0])/rng if rng>0 else 0.0

    for i in range(n):
        if i < WARMUP or not (SIG_START <= tod[i] <= SIG_LAST): continue
        s = sig[i]
        if s <= 0: continue
        side = -1 if c[i] >= vwap[i]+K*s else (+1 if c[i] <= vwap[i]-K*s else 0)
        if side == 0: continue
        entry = c[i]; stopdist = (K_STOP-K)*s; risk = stopdist
        # short(side=-1): stop ABOVE entry; long(side=+1): stop BELOW entry
        stop = entry + (-side)*stopdist
        exits = {}
        for tm in TARGETS:
            target = entry + side*tm*s          # short->below, long->above
            ep = c[-1]
            for j in range(i+1, n):
                if side == -1:
                    if h[j] >= stop:   ep = stop;   break
                    if l[j] <= target: ep = target; break
                else:
                    if l[j] <= stop:   ep = stop;   break
                    if h[j] >= target: ep = target; break
            exits[tm] = side*(ep-entry)         # gross points
        return dict(date=g.date.iloc[0], risk=risk, D=D, **{f"g{tm}":exits[tm] for tm in TARGETS})
    return None


def line(R, lbl):
    n=len(R); m=R.mean(); sd=R.std(); t=m/(sd/np.sqrt(n)) if sd>0 else 0
    return f"E[R]={m:+.3f}(t={t:+.2f}) win={ (R>0).mean()*100:4.1f}%"


def main():
    df = load()
    rows=[r for _,g in df.groupby("date",sort=True) if (r:=simulate_day(g))]
    t = pd.DataFrame(rows); t["yr"]=pd.to_datetime(t["date"]).dt.year
    print(f"entries: {len(t)} days | cost {RT_COST_PTS}pt | band {K}s stop {K_STOP}s\n")
    print(f"{'target':>8} | {'NET all':<34} | {'NET out-sample 23-24':<34} | GROSS all")
    print("-"*112)
    for tm in TARGETS:
        gp = t[f"g{tm}"]
        net = (gp - RT_COST_PTS)/t["risk"]
        grs = gp/t["risk"]
        out = (t.loc[t.yr>=2023, f"g{tm}"] - RT_COST_PTS)/t.loc[t.yr>=2023,"risk"]
        tag = "  (full->VWAP)" if tm==2.0 else ("  (scalp)" if tm==0.5 else "")
        print(f"{tm:>6}σ{tag:<2}| {line(net,'')} | {line(out,''):<34} | {line(grs,'')[:18]}")
    # day-type diagnostic for the scalp (smallest target)
    print("\ndiagnostic for SCALP (0.5σ target), net E[R] by full-day type (postfactum):")
    t["Dbin"]=pd.cut(t["D"],[0,.35,.6,1.01],labels=["rotational","slow_trend","fast_trend"])
    net05=(t["g0.5"]-RT_COST_PTS)/t["risk"]
    d=t.assign(R=net05).groupby("Dbin",observed=True)["R"].agg(n="size",ER="mean",win=lambda s:(s>0).mean()).round(3)
    print(d.to_string())
    t.to_csv("/mnt/user-data/outputs/dax_step2b_fade_sweep.csv",index=False)
    print("\nsaved -> dax_step2b_fade_sweep.csv")


if __name__=="__main__":
    main()
