# EPICK 프론트엔드

React + TypeScript로 구현한 EPICK 화면 검토용 프론트엔드입니다. 기업 탐색 → 공고에서 지원 준비 → 문항 입력 → 경험 비교·소재 선택 흐름과 경험 보관함이 있습니다.

## 설치와 실행

검증 환경: macOS, Node.js 24.13.1. `.nvmrc`는 Node 24를 지정합니다. Windows/Linux는 별도 실행 검증을 하지 않았습니다.

```sh
npm ci
npm run dev -- --port 3001
```

http://localhost:3001/ 을 엽니다. 포트가 사용 중이면 터미널에 표시되는 주소를 확인하거나 `--port 3002`처럼 변경합니다. 기본 `npm run dev` 포트는 5173입니다. 종료는 Ctrl+C입니다.

```sh
npx tsc --noEmit
npm run build
```

개발 화면 검토에는 `npm run dev`를 사용하세요. 기존 `start` 스크립트는 빌드 후 Cloudflare 로컬 실행용으로 남아 있으며, 공개 배포·FastAPI 운영 구성과는 별도입니다. 첫 실행의 모의 인증 관련 로그도 팀의 Google 인증 연결을 뜻하지 않습니다.

## 코드 위치

| 파일/폴더 | 역할 |
| --- | --- |
| `app/page.tsx` | 메뉴, 화면 전환, 지원 준비 이동 |
| `app/company-explorer.tsx` | 기업·근거·채용 공고 화면 |
| `app/features.tsx` | W1 Activity/Episode, Project/Question, 비동기 Job 및 추천·소재 선택 화면 |
| `app/demo-state.tsx` | 명시적 개발 fixture provider 호환 경계(개인 데이터 저장 없음) |
| `lib/api/`, `lib/queries/` | 생성된 OpenAPI 타입을 사용하는 W1 공개 API 클라이언트와 React Query 상태 |
| `app/figma-theme.css` | 민트·차콜 디자인 토큰과 현재 화면 테마 |
| `app/globals.css` | 레이아웃과 초기 레거시 스타일. 테마 우선순위 확인 필요 |
| `components/ui/` | 드롭다운·다이얼로그 등 공통 UI |
| `public/figma`, `public/fonts` | 디자인 에셋과 폰트·라이선스 |
| `scripts/`, `build/`, `vite.config.ts` | 기존 Vinext/Vite 실행·빌드 지원 |
| `app/chatgpt-auth.ts`, `db/`, `drizzle/`, `examples/` | 스타터 보조 코드. EPICK 인증·DB/API 구현으로 사용하지 않음 |

## 현재 동작 및 제한

- 경험 등록·수정·임시 저장·검색과 프로젝트·문항 생성·수정은 W1 공개 API를 통해 PostgreSQL에 저장됩니다.
- 개인 데이터는 localStorage에 저장하지 않습니다. 개발 fixture는 비프로덕션에서 `NEXT_PUBLIC_EPICK_DEMO_FIXTURES=true`를 명시한 경우에만 메모리 provider로 활성화됩니다.
- 기업 정보·채용 공고·추천 내용은 디자인 및 모의 데이터입니다. 실제 수집·AI 분석·조건별 순위 계산을 하지 않습니다.
- 추가한 기업과 기업 해석 활용 선택은 현재 페이지 세션에만 유지됩니다.
- 오른쪽 SNS 아이콘은 현재 클릭 동작이 없습니다. 검증된 외부 주소를 연결하지 않았습니다.
- Google OIDC, Activity/Episode, Project/Question, 비동기 Job 및 버전 있는 추천·소재 선택은 W1 공개 API와 연동됐습니다. 삭제 및 기업 분석의 서버 전환은 후속 사용자 스토리 범위입니다.
- 추천 결과는 `SYNTHETIC`과 `ENGINE` 출처를 항상 표시하며, 변경된 문항 버전이나 사용할 수 없는 결과는 선택할 수 없습니다.
- 미완료 경험·프로젝트는 `/home`과 `/resume-items` 응답으로 복원합니다.

## 팀 통합 기준

현재 Vinext/React Query 기반 클라이언트는 `NEXT_PUBLIC_W1_API_URL`의 W1 공개 `/api/v1/*` 계약만 호출합니다. W2/W3/W4 내부 주소를 브라우저에 노출하지 않습니다. 설치 버전은 잠금 파일을 기준으로 유지하세요.

디자인 규칙: [DESIGN_SYSTEM.md](./DESIGN_SYSTEM.md). 새 UI는 공통 토큰을 사용하고 기존 파란색 하드코딩을 추가하지 않습니다.
