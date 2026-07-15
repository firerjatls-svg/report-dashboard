#!/usr/bin/env python3
"""Google Drive 공개 폴더의 리포트를 저장소로 동기화하고 master.json을 갱신.

GitHub Actions가 매시간 실행. 흐름:
  1) Drive 폴더의 .html 목록 조회 (API 키 + 링크 공개 폴더)
  2) 신규/변경 파일을 reports/ 로 다운로드
  3) 리포트 HTML의 <script id="dashboard-meta"> 블록을 읽어 분류 정보 획득
     · 블록이 있으면 → 섹터·강도·변수·촉매까지 채워 '정식 등록'
     · 블록이 없으면 → 파일명 종목코드로 '미분류(검수 필요)' 등록
  4) 기존 종목의 개정판이면 리포트 파일·기준일만 교체 (Surgical)
  5) 종목코드를 블록에도 파일명에도 못 찾으면 건너뜀 (오등록 방지)

메타는 리포트 생성 시 프롬프트로 심어진다 → 봇은 파싱만 하므로 API 키 불필요.
환경변수: GDRIVE_API_KEY, GDRIVE_FOLDER_ID
상태 파일: data/sync_state.json (fileId → modifiedTime)
"""
import json, os, re, sys, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
STATE_F = ROOT / "data/sync_state.json"
MASTER_F = ROOT / "data/master.json"
PALETTE = ["#7C2D12", "#1E3A8A", "#3F3F46", "#701A75", "#14532D",
           "#7F1D1D", "#0C4A6E", "#4C1D95", "#78350F", "#831843"]
ETC_SECTOR = {"id": "etc", "name": "미분류 (자동 등록)",
              "factor": "메타 블록 없이 등록 — 섹터·스코어·촉매는 검수 후 수정"}


def http_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def drive_list(folder_id, key):
    q = urllib.parse.quote(f"'{folder_id}' in parents and trashed=false")
    url = (f"https://www.googleapis.com/drive/v3/files?q={q}&key={key}"
           f"&fields=files(id,name,modifiedTime)&pageSize=1000")
    return [f for f in http_json(url)["files"] if f["name"].lower().endswith(".html")]


def drive_download(file_id, key, dest: Path):
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media&key={key}"
    with urllib.request.urlopen(url, timeout=120) as r:
        dest.write_bytes(r.read())


def parse_meta(html_text, sector_ids):
    """리포트 HTML에서 <script id="dashboard-meta"> JSON 블록을 읽어 dict 반환. 없으면 None."""
    m = re.search(
        r'<script[^>]*id=["\']dashboard-meta["\'][^>]*>(.*?)</script>',
        html_text, re.S | re.I)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        print("  dashboard-meta 블록 파싱 실패 — JSON 형식 오류")
        return None

    def as_score(v):
        try:
            return max(1, min(3, int(v)))
        except (TypeError, ValueError):
            return 2

    sector = data.get("sector")
    if sector not in sector_ids:
        sector = None  # 정식 섹터가 아니면 미분류 처리로 넘김
    cats = data.get("catalysts") if isinstance(data.get("catalysts"), list) else []
    cats = [c for c in cats if isinstance(c, dict) and c.get("date")]
    return {
        "code": (str(data["code"]).strip() if data.get("code") else None),
        "name": data.get("name"),
        "sector": sector,
        "intensity": as_score(data.get("intensity")),
        "variables": as_score(data.get("variables")),
        "note": data.get("note") or "메타 등록 (초안)",
        "desc": data.get("desc"),
        "tags": data.get("tags"),
        "catalysts": cats,
    }


def upsert(master, code, name, fname, mdate, meta):
    for c in master["companies"]:
        if c["code"] == code:  # 기존 종목 — 리포트 파일·기준일만 교체
            c["report"] = {"file": fname, "date": mdate}
            print(f"  갱신: {c['name']} ← {fname}")
            return

    m = meta or {}
    # 유효한 섹터 메타가 있으면 정식 등록, 없으면 미분류(검수 대기)
    classified = bool(m.get("sector"))
    sector = m.get("sector") or "etc"
    if not classified and all(s["id"] != "etc" for s in master["sectors"]):
        master["sectors"].append(ETC_SECTOR)

    used = {c["ac"] for c in master["companies"]}
    entry = {
        "code": code,
        "name": m.get("name") or name,
        "sector": sector,
        "seq": max((c["seq"] for c in master["companies"]), default=0) + 1,
        "ac": next((p for p in PALETTE if p not in used), "#525252"),
        "desc": m.get("desc") or "메타 블록 없음 — 설명 검수 필요",
        "tags": m.get("tags") or name,
        "per": None,
        "frame": {
            "intensity": m.get("intensity", 2),
            "variables": m.get("variables", 2),
            "note": m.get("note") or "자동 등록 — 검수 전 기본값",
        },
        "catalysts": m.get("catalysts") or [],
        "report": {"file": fname, "date": mdate},
    }
    if not classified:
        entry["auto"] = True  # 미분류만 검수 뱃지
    master["companies"].append(entry)
    print(f"  신규: {code} {entry['name']} → 섹터 {sector} "
          f"({'메타 등록' if classified else '미분류'})")


def main():
    key, folder = os.environ.get("GDRIVE_API_KEY"), os.environ.get("GDRIVE_FOLDER_ID")
    if not (key and folder):
        print("GDRIVE_API_KEY / GDRIVE_FOLDER_ID 미설정 — 동기화 생략")
        return 0
    state = json.loads(STATE_F.read_text()) if STATE_F.exists() else {}
    master = json.loads(MASTER_F.read_text(encoding="utf-8"))
    sector_ids = [s["id"] for s in master["sectors"]]
    changed, skipped = 0, []

    for f in drive_list(folder, key):
        if state.get(f["id"]) == f["modifiedTime"]:
            continue
        print(f"수신: {f['name']}")
        REPORTS.mkdir(exist_ok=True)
        dest = REPORTS / f["name"]
        drive_download(f["id"], key, dest)

        html_text = dest.read_text(encoding="utf-8", errors="ignore")
        meta = parse_meta(html_text, sector_ids)

        fn = re.match(r"(\d{6})_([^_\.]+)", f["name"])
        code = (meta or {}).get("code") or (fn.group(1) if fn else None)
        name = (meta or {}).get("name") or (fn.group(2) if fn else f["name"].rsplit(".", 1)[0])

        if not code or not re.fullmatch(r"\d{6}", str(code)):
            print("  종목코드 불명 — 건너뜀 (파일명 '코드_종목명.html' 또는 메타 블록 필요)")
            dest.unlink()
            skipped.append(f["name"])
            continue

        upsert(master, str(code), name, f["name"], f["modifiedTime"][:10], meta)
        state[f["id"]] = f["modifiedTime"]
        changed += 1

    if changed:
        MASTER_F.write_text(json.dumps(master, ensure_ascii=False, indent=1), encoding="utf-8")
        STATE_F.write_text(json.dumps(state, indent=1), encoding="utf-8")
    print(f"완료: 반영 {changed}건, 건너뜀 {skipped or '없음'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
