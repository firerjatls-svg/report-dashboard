# 기업분석 리포트 시리즈 — 웹 대시보드

Drive 폴더에 리포트를 올리면 사이트에 자동 반영되고, 시세는 매 평일 아침 자동 갱신.

## 반영 흐름 (봇 2개)
```
[리포트 봇 · 매시간]  Google Drive 폴더 → 새 .html 감지 → reports/ 다운로드
                      → master.json 자동 갱신 → 커밋 → Vercel 재배포
[시세 봇 · 평일 아침]  KRX(marcap) → data/quotes.json → 커밋 → Vercel 재배포
```

## 구조
```
index.html                  대시보드 (보드 / 3질문 스코어보드 / 촉매 캘린더)
data/master.json            정성 데이터 — 사람/Claude 편집 + 리포트 봇이 항목 추가
data/quotes.json            시세 — 시세 봇 전용 (손으로 편집 금지)
data/sync_state.json        리포트 봇 상태 파일 (편집 금지)
scripts/update_quotes.py    시세 봇
scripts/sync_reports.py     리포트 봇 (Drive → 저장소)
.github/workflows/          스케줄 2개 (update-quotes / sync-reports)
reports/                    리포트 HTML (봇이 채움; 직접 넣어도 됨)
```

## 최초 설정 (1회, 약 20분)
1. **GitHub**: 새 저장소에 이 폴더 업로드 → Settings → Actions → General
   → Workflow permissions → **Read and write**
2. **Vercel**: Add New Project → 저장소 Import → Deploy → `https://….vercel.app` 발급
3. **Google Drive**: 리포트 전용 폴더 생성 → 공유: **링크가 있는 모든 사용자(뷰어)**
   → 폴더 URL의 `folders/` 뒤 문자열 = FOLDER_ID
4. **API 키**: console.cloud.google.com → 프로젝트 생성 → "Google Drive API" 사용 설정
   → 사용자 인증 정보 → API 키 생성
5. **GitHub Secrets** (저장소 Settings → Secrets and variables → Actions):
   - `GDRIVE_API_KEY`, `GDRIVE_FOLDER_ID` (필수)
   - `ANTHROPIC_API_KEY` (선택 — 파일명 규칙 없는 리포트의 메타데이터 추출용)
6. Actions 탭에서 두 워크플로를 Run workflow로 한 번씩 수동 실행해 확인

## 사용 규칙
- **리포트 파일명 규칙**: `종목코드_종목명_….html` (예: `000660_SK하이닉스_분석.html`)
  → 이 규칙만 지키면 어느 LLM 산출물이든 100% 자동 등록.
  규칙이 없으면 ANTHROPIC_API_KEY가 있을 때만 본문 추출을 시도하고, 실패 시 건너뜀.
- 자동 등록 종목은 "미분류" 섹터 + `자동 등록 · 검수 필요` 뱃지로 표시됨.
  검수: master.json에서 sector / desc / frame / catalysts 수정 후 `"auto": true` 삭제.
- 기존 종목의 개정판 리포트를 올리면(같은 종목코드) 항목 추가 없이 리포트 링크·기준일만 교체됨.

## 주의
- Drive 폴더와 이 저장소는 사실상 공개다 — 직접 작성한 리포트만 넣을 것.
  증권사 PDF·IR 원문 등 외부 저작물 금지 (저작권).
- 로컬 미리보기: `python -m http.server` 후 localhost:8000
- 시세 원천은 T+1~2일 지연 — 화면에 기준일 명시, 5일 초과 시 경고 뱃지 자동 표시.
