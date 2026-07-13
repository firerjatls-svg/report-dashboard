#!/usr/bin/env python3
"""marcap(KRX 아카이브)에서 master.json의 전 종목 시세를 뽑아 data/quotes.json 생성.

GitHub Actions가 매 평일 아침 실행. 사람은 이 파일을 편집하지 않는다.
출력: {asof, generated, quotes: {code: {close, chg, mcap, w52lo, w52hi}}}
- close/chg: 최근 거래일 종가·전일 대비 등락률 (marcap은 T+1~2일 지연)
- mcap: 억원, w52lo/hi: 최근 365일 저가/고가
"""
import json, sys, datetime, urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
MARCAP = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{y}.parquet"


def load_year(y: int) -> pd.DataFrame | None:
    path = Path(f"/tmp/marcap-{y}.parquet")
    if not path.exists():
        try:
            urllib.request.urlretrieve(MARCAP.format(y=y), path)
        except Exception as e:  # 연초에 전년도만 있을 수 있음
            print(f"skip {y}: {e}")
            return None
    return pd.read_parquet(path)


def main() -> int:
    master = json.loads((ROOT / "data/master.json").read_text(encoding="utf-8"))
    codes = [c["code"] for c in master["companies"]]

    year = datetime.date.today().year
    frames = [df for y in (year - 1, year) if (df := load_year(y)) is not None]
    if not frames:
        print("marcap 데이터를 받지 못함")
        return 1
    df = pd.concat(frames)
    latest_date = df["Date"].max()
    window = df[df["Date"] >= latest_date - pd.Timedelta(days=365)]

    quotes, missing = {}, []
    for code in codes:
        g = window[window["Code"] == code]
        if g.empty:
            missing.append(code)
            continue
        last = g[g["Date"] == g["Date"].max()].iloc[0]
        lows = g[g["Low"] > 0]["Low"]
        quotes[code] = {
            "close": int(last["Close"]),
            "chg": round(float(last["ChangesRatio"]), 2),
            "mcap": round(float(last["Marcap"]) / 1e8),
            "w52lo": int(lows.min()) if len(lows) else int(last["Close"]),
            "w52hi": int(g["High"].max()),
        }

    out = {
        "asof": latest_date.strftime("%Y-%m-%d"),
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "quotes": quotes,
    }
    (ROOT / "data/quotes.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"asof={out['asof']} 종목={len(quotes)} 누락={missing or '없음'}")
    return 0 if not missing else 1  # 신규 상장 직후 등 누락 시 실패로 알림


if __name__ == "__main__":
    sys.exit(main())
