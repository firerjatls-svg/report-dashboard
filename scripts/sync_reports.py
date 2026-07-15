#!/usr/bin/env python3
"""Google Drive 공개 폴더의 리포트 파일을 저장소로 동기화하고 master.json을 갱신.

GitHub Actions가 매시간 실행. 흐름:
  1) Drive 폴더의 .html 파일 목록 조회 (API 키 + 링크 공개 폴더)
  2) 신규/변경 파일을 reports/ 로 다운로드
  3) 파일명에서 종목코드(6자리) 파싱 — 규칙: 종목코드_종목명_*.html
     · 기존 종목이면 report.file / report.date 만 갱신 (Surgical)
     · 신규 종목이면 master.json에 '자동 등록' 항목 추가
       (ANTHROPIC_API_KEY 가 있으면 본문에서 섹터·설명·태그 추출, 없으면 최소 항목)
  4) 코드가 파일명에도 본문에서도 안 나오면 건너뛰고 로그 (오등록 방지)

환경변수: GDRIVE_API_KEY, GDRIVE_FOLDER_ID, ANTHROPIC_API_KEY(선택)
상태 파일: data/sync_state.json (fileId → modifiedTime)
"""
import json, os, re, sys, html, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
STATE_F = ROOT / "data/sync_state.json"
MASTER_F = ROOT / "data/master.json"
PALETTE = ["#7C2D12", "#1E3A8A", "#3F3F46", "#701A75", "#14532D",
           "#7F1D1D", "#0C4A6E", "#4C1D95", "#78350F", "#831843"]
ETC_SECTOR = {"id": "etc", "name": "미분류 (자동 등록)",
              "factor": "동기화 봇이 등록 — 섹터·스코어·촉매는 검수 후 master.json에서 수정"}


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


def strip_tags(html_text, limit=8000):
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))[:limit]


def extract_meta_via_claude(text, sector_ids):
    """본문에서 종목 메타데이터 추출. 실패 시 None (봇은 지어내지 않는다)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    prompt = (
        "다음은 한국 상장기업 분석 리포트 본문이다. 리포트의 논지를 읽고 아래 스키마의 JSON만 "
        "출력하라(설명·백틱 금지). "
        '{"code":"6자리 종목코드 또는 null","name":"종목명",'
        '"sector":"다음 섹터 id 중 하나 ' + str(sector_ids) + ' 중 가장 적합한 것",'
        '"desc":"리포트의 핵심 투자 명제 한 문장(50자 내외)",'
        '"tags":"검색 키워드 5~8개 공백 구분",'
        '"intensity":"증익 사이클 강도 1~3 정수(3=가장 강함)",'
        '"variables":"경로 변수/리스크 수 1~3 정수(1=적음,3=많음)",'
        '"note":"강도·변수 판단 근거 한 구절(20자 내외)",'
        '"catalysts":[{"date":"YYYY-MM-DD","label":"다음 촉매 이벤트(분기 실적 등)"}]} '
        "규칙: sector는 반드시 제시된 id 중 하나를 고를 것(모르면 가장 근접한 것). "
        "intensity/variables는 리포트의 결론 강도에 근거해 판단. "
        "catalysts는 리포트에 언급된 다음 실적·수주·공시 일정을 추정해 1~2개, 없으면 빈 배열. "
        "code나 name이 불확실하면 그 필드만 null. 본문:\n" + text
    )
    body = json.dumps({"model": "claude-sonnet-4-6", "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
        raw = "".join(b.get("text", "") for b in out["content"])
        return json.loads(re.sub(r"^```json|```$", "", raw.strip(), flags=re.M).strip())
    except Exception as e:
        print(f"  메타 추출 실패: {e}")
        return None


def upsert(master, code, name, fname, mdate, meta):
    for c in master["companies"]:
        if c["code"] == code:  # 기존 종목 — 리포트 파일·기준일만 교체
            c["report"] = {"file": fname, "date": mdate}
            print(f"  갱신: {c['name']} ← {fname}")
            return
    sector = (meta or {}).get("sector") or "etc"
    if sector not in {s["id"] for s in master["sectors"]}:
        sector = "etc"
    if sector == "etc" and all(s["id"] != "etc" for s in master["sectors"]):
        master["sectors"].append(ETC_SECTOR)
    used = {c["ac"] for c in master["companies"]}
    m = meta or {}

    def as_score(v, lo=1, hi=3, default=2):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return default

    ai_scored = m.get("intensity") is not None and m.get("variables") is not None
    intensity = as_score(m.get("intensity"))
    variables = as_score(m.get("variables"))
    note = m.get("note") or ("AI 분류 (초안)" if ai_scored else "자동 등록 — 검수 전 기본값")
    catalysts = m.get("catalysts") if isinstance(m.get("catalysts"), list) else []
    catalysts = [c for c in catalysts if isinstance(c, dict) and c.get("date")]

    master["companies"].append({
        "code": code,
        "name": m.get("name") or name,
        "sector": sector,
        "seq": max((c["seq"] for c in master["companies"]), default=0) + 1,
        "ac": next((p for p in PALETTE if p not in used), "#525252"),
        "desc": m.get("desc") or "자동 등록 — 설명 검수 필요",
        "tags": m.get("tags") or name,
        "per": None,
        "frame": {"intensity": intensity, "variables": variables, "note": note},
        "catalysts": catalysts,
        # AI가 섹터+스코어까지 판단했으면 정식 등록, 아니면 검수 대기(auto)
        **({} if ai_scored and sector != "etc" else {"auto": True}),
        "report": {"file": fname, "date": mdate},
    })
    tag = "AI 분류" if ai_scored else "자동 등록"
    print(f"  신규: {code} {master['companies'][-1]['name']} → 섹터 {sector} ({tag})")


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
        m = re.match(r"(\d{6})_([^_\.]+)", f["name"])
        code = m.group(1) if m else None
        name = m.group(2) if m else f["name"].rsplit(".", 1)[0]
        is_existing = code and any(c["code"] == code for c in master["companies"])
        meta = None
        # 신규 종목이면(코드 유무 무관) 본문을 Claude에게 읽혀 섹터·스코어·촉매까지 판단.
        # 기존 종목의 개정판이면 리포트 파일만 교체하므로 API 호출 불필요.
        if not is_existing:
            meta = extract_meta_via_claude(
                strip_tags(dest.read_text(encoding="utf-8", errors="ignore")), sector_ids)
        if not code:
            code = (meta or {}).get("code")
        if not code or not re.fullmatch(r"\d{6}", str(code)):
            print("  종목코드 불명 — 건너뜀 (파일명을 '종목코드_종목명_….html'로)")
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
