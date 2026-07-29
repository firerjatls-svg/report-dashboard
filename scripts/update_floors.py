#!/usr/bin/env python3
"""floors.json(판단) + marcap(시세) -> data/rockbottom.json (락바텀 FWD 표 자동 최신화)
update_quotes.py와 동일 패턴. GitHub Actions에서 매일 실행하면 Claude 토큰 0으로 표가 갱신됨.
F12M = w1*np26 + w2*np27 (가중치는 floors.json의 fy_weights, 27 결측 시 26 flat)
"""
import json, datetime, urllib.request
from pathlib import Path
import pandas as pd

MARCAP = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"

def load_marcap(y):
    p = Path(f"/tmp/marcap-{y}.parquet")
    if not p.exists():
        urllib.request.urlretrieve(MARCAP.format(y=y), p)
    return pd.read_parquet(p, columns=["Code", "Close", "Date", "Stocks"])

def main():
    fl = json.load(open("data/floors.json", encoding="utf-8"))
    w1 = eval(fl["fy_weights"]["fy1"]); w2 = eval(fl["fy_weights"]["fy2"])
    floors = fl["floors"]

    y = datetime.date.today().year
    df = load_marcap(y)
    df = df[df["Code"].isin(floors)]
    df["Date"] = df["Date"].astype(str)
    last = df["Date"].max()
    snap = df[df["Date"] == last].set_index("Code")

    def f12(n26, n27):
        if n26 is None: return None
        return w1 * n26 + w2 * (n27 if n27 else n26)

    rows = []
    for code, f in floors.items():
        if code not in snap.index: continue
        px = float(snap.loc[code, "Close"]); sh = float(snap.loc[code, "Stocks"])
        per = f["floor_per"]
        out = {"code": code, "name": f["name"], "close": int(px), "grade": f["grade"],
               "floor_per": per, "b_src": f["b_src"]}
        for tag in ("a", "b"):
            fv = f12(f[f"np26_{tag}"], f[f"np27_{tag}"])
            if fv is None:
                out[tag] = None; continue
            npw = fv * 1e8
            floor_px = per * npw / sh
            out[tag] = {"f12m": round(fv), "per_now": round(px * sh / npw, 1),
                        "floor": int(floor_px), "gap_pct": round((floor_px / px - 1) * 100, 1)}
        rows.append(out)

    rows.sort(key=lambda r: (r["b"] or r["a"])["gap_pct"], reverse=True)
    result = {"asof": last, "params_asof": fl["asof"], "generated": datetime.datetime.utcnow().isoformat() + "Z",
              "rows": rows}
    Path("data").mkdir(exist_ok=True)
    json.dump(result, open("data/rockbottom.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"rockbottom.json: {len(rows)} rows, price asof {last}")

if __name__ == "__main__":
    main()
