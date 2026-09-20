# Feature Specification: Frontend–W1 Backend Integration

**Feature Branch**: `006-frontend-backend-integration`

**Created**: 2026-09-20

**Status**: Draft

**Input**: User description: "EPICK의 완성된 화면 구현을 W1 FastAPI 공개 API와 직접 연동하고, Google/OIDC 인증부터 경험·프로젝트·Job·추천·선택·삭제 흐름까지 W1이 통합 구현한다. 프런트는 W2·W3·W4에 직접 접근하지 않는다."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Google 로그인과 개인 작업공간 복구 (Priority: P1)

사용자는 Google 계정으로 로그인하고 자신의 경험, 지원 프로젝트, 이어할 작업만 조회한다. 다른 사용자의 데이터는 존재 여부까지 알 수 없다.

**Why this priority**: 모든 개인 데이터 API와 사용자별 작업의 보안 경계이며, 이후 흐름의 선행조건이다.

**Independent Test**: 두 개의 테스트 계정으로 로그인하여 각 계정의 홈·경험·프로젝트가 분리되고 비인증 요청은 거절되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** 유효한 Google 계정과 등록된 OAuth client가 있을 때, **When** 사용자가 로그인하면, **Then** W1은 검증된 OIDC identity를 내부 사용자에 연결하고 안전한 애플리케이션 세션을 발급한다.
2. **Given** 두 사용자가 서로 다른 개인 데이터를 보유할 때, **When** 각 사용자가 홈과 리소스를 조회하면, **Then** 자신의 데이터만 보며 타인 리소스와 미존재 리소스는 동일한 404 형태로 응답된다.
3. **Given** 만료되거나 폐기된 세션일 때, **When** 개인 API를 호출하면, **Then** 401이 반환되고 프런트는 재로그인을 안내한다.

---

### User Story 2 - 경험과 지원 프로젝트의 서버 저장 (Priority: P1)

사용자는 현재 로컬 데모 화면에서 경험, 프로젝트, 문항을 생성·수정·조회하며 새로고침이나 다른 기기에서도 서버에 저장된 최신 데이터를 복구한다.

**Why this priority**: localStorage 모의를 실제 제품 데이터로 전환하는 최소 기능이다.

**Independent Test**: 로그인 후 경험과 프로젝트를 생성·수정하고 브라우저 저장소를 비운 뒤 재접속해 서버 상태가 복구되는지 검증한다.

**Acceptance Scenarios**:

1. **Given** 로그인한 사용자일 때, **When** 경험을 임시 저장하거나 완료하고 프로젝트와 문항을 생성하면, **Then** W1 PostgreSQL에 소유권과 버전이 보존된다.
2. **Given** 기존 데이터가 있을 때, **When** 화면을 새로 열면, **Then** 프런트는 localStorage seed가 아니라 W1 조회 응답으로 화면을 구성한다.
3. **Given** 두 화면에서 같은 리소스를 수정했을 때, **When** 오래된 버전으로 저장하면, **Then** 조용히 덮어쓰지 않고 충돌을 안내한다.

---

### User Story 3 - 비동기 분석 상태와 사용자 행동 (Priority: P1)

사용자는 분석 요청이 접수·dispatch·실행·수집·검색 반영·추천 중 어느 상태인지 확인하고, 서버가 허용한 경우에만 재시도·제한 진행·중단을 선택한다.

**Why this priority**: `202 Accepted`를 완료로 오인하지 않고 W2~W4 비동기 실행을 안전하게 표시하기 위한 핵심 흐름이다.

**Independent Test**: 합성 worker fixture로 QUEUED, WAITING_USER, PAUSED_RATE_LIMIT, SUCCEEDED, FAILED_RETRYABLE, CANCELLED 전이를 만들고 화면과 명령을 검증한다.

**Acceptance Scenarios**:

1. **Given** 분석 요청이 수락됐을 때, **When** W1이 202를 반환하면, **Then** 화면은 요청 접수와 실제 dispatch·실행을 구분한다.
2. **Given** Job에 열린 required action이 있을 때, **When** 사용자가 서버가 제공한 행동을 선택하면, **Then** 프런트는 예상 input/result version과 멱등성 키를 포함해 한 번만 제출한다.
3. **Given** 네트워크가 일시적으로 끊겼을 때, **When** polling이 실패하면, **Then** 작업 실패로 단정하거나 자동 재실행하지 않고 재연결 후 현재 Job을 다시 조회한다.

---

### User Story 4 - 추천 후보 비교와 소재 선택 저장 (Priority: P2)

사용자는 사용 가능한 추천 결과만 조회하고 후보의 근거·강점·한계를 비교한 후 문항별 소재를 저장한다.

**Why this priority**: 실제 EPICK 사용자 가치를 완성하지만 인증·CRUD·Job 상태 연결 뒤에 검증할 수 있다.

**Independent Test**: 고정된 synthetic recommendation fixture로 후보 목록, 제한 결과, 빈 결과, 선택 생성·교체·삭제를 검증한다.

**Acceptance Scenarios**:

1. **Given** 추천 run이 READY 또는 허용된 LIMITED 결과일 때, **When** 후보를 열면, **Then** result version과 근거·한계가 표시된다.
2. **Given** 선택 가능한 후보일 때, **When** 사용자가 소재를 저장하면, **Then** 재접속 후에도 같은 문항의 선택이 복구된다.
3. **Given** 결과가 제한·stale·사용 불가일 때, **When** 이전 응답이나 캐시가 늦게 도착하면, **Then** 프런트는 해당 후보를 다시 노출하거나 선택 가능하게 만들지 않는다.

---

### User Story 4.5 - W4 추천 실행 경로 연결 (Priority: P2)

사용자는 기존 W1 추천 API를 그대로 사용하면서, 서버가 ENGINE 모드로 승인한 실행에서는 실제 W4 추천 코드가 만든 결과를 W1의 현재성·권한 검사를 거쳐 조회한다. 브라우저는 W4나 queue를 직접 알지 못한다.

**Why this priority**: US4는 저장된 synthetic 결과의 화면·선택 계약을 검증했다. 삭제/기업 조회인 US5 전에 실제 추천 실행 경계를 연결해야 합성 fixture와 W4 실행 결과가 제품 흐름에서 뒤섞이지 않는다.

**Independent Test**: 고정 합성 입력과 모의 모델을 사용하되 실제 W4 `W1ExecutionAdapter` 경로를 실행해 W1 공개 추천 API에서 `result_origin=ENGINE`, `LIMITED`, 후보와 선택 결과를 확인한다. 같은 실행의 중복 전달, 취소, snapshot 변경, owner deletion epoch 변경, Source 현재성 변경은 게시 효과가 한 번 이하이거나 0회여야 한다.

**Acceptance Scenarios**:

1. **Given** 서버가 ENGINE 실행을 허용하고 고정된 합성 Run을 접수했을 때, **When** W1 outbox/SQS 명령을 W4 worker가 처리하면, **Then** W4는 W1의 보호된 내부 HTTP로만 context·권한을 조회하고 결과를 원자적으로 게시하며 공개 API는 기존 응답 형태를 유지한다.
2. **Given** 동일 실행 메시지가 중복 전달되거나 결과 게시 전 권한·삭제·문항·snapshot·Source revision이 바뀌었을 때, **When** W4가 게시를 시도하면, **Then** W1은 stale 결과와 부분 후보 저장을 거부하고 취소/최신 상태를 되살리지 않는다.
3. **Given** W4 실행·모델·내부 HTTP가 실패했을 때, **When** 실행 결과를 조회하면, **Then** 안전한 실패/재시도 상태를 반환하고 동일 Run을 SYNTHETIC 결과로 조용히 대체하지 않는다.
4. **Given** 실제 W4 코드가 합성 자료를 처리했을 때, **When** 사용자가 결과를 조회하면, **Then** 실행 출처는 `ENGINE`이지만 입력·정책·모델 제한은 `LIMITED`로 함께 표시되며 REAL 사용자 자료 승인으로 오해되지 않는다.

---

### User Story 5 - 삭제·로그아웃·안전한 오류 복구 (Priority: P2)

사용자는 경험·프로젝트·계정을 정해진 범위로 삭제하고, 로그아웃 후 개인 캐시가 남지 않으며, 안전한 오류 코드에 따라 복구한다.

**Why this priority**: 개인 데이터와 삭제 효력은 배포 전 반드시 검증해야 하는 제품·보안 요구다.

**Independent Test**: 삭제 요청 직후 늦은 조회 응답과 재접속을 발생시켜 삭제된 데이터가 복원되지 않는지 검증한다.

**Acceptance Scenarios**:

1. **Given** 삭제 범위를 확인한 사용자일 때, **When** 삭제 요청을 제출하면, **Then** 프런트는 즉시 대상 캐시를 격리하고 서버 완료 전에는 삭제 완료라고 표시하지 않는다.
2. **Given** 삭제 전 시작된 요청이 있을 때, **When** 늦은 성공 응답이 도착하면, **Then** 삭제 epoch/version보다 오래된 응답은 화면과 캐시에 반영되지 않는다.
3. **Given** 사용자가 로그아웃했을 때, **When** 같은 브라우저에서 로그인 화면으로 돌아가면, **Then** 개인 query cache와 화면 상태가 제거된다.

## Edge Cases

- Google callback의 state/nonce/PKCE verifier가 누락·불일치하거나 redirect URI가 다르면 로그인에 실패하고 세션을 만들지 않는다.
- OAuth client secret, authorization code, 세션 원문, 개인 입력은 로그와 사용자 오류 응답에 포함하지 않는다.
- 동일 mutation이 중복 클릭·재전송되면 같은 `Idempotency-Key` 범위에서 중복 리소스를 만들지 않는다.
- polling 응답 순서가 뒤바뀌면 더 오래된 `updated_at`/version의 응답이 최신 상태를 덮어쓰지 않는다.
- 202 수락 후 dispatch가 BLOCKED인 경우 실행 중이나 성공으로 표시하지 않는다.
- W3 restriction/mismatch/replay 계약이 확정되지 않은 상태는 새로운 public enum을 발명하지 않고 기존 Job/결과의 안전한 제한 상태로 표시한다.
- 추천 결과 없음은 시스템 실패와 구분하고, 경험 추가 후 원 작업으로 돌아갈 수 있어야 한다.
- Analytics 동의가 꺼져 있어도 핵심 저장·추천·선택 기능은 동작하며 평가 이벤트를 보내지 않는다.
- 프런트 origin이 허용 목록에 없거나 credentialed CORS 규칙을 위반하면 요청을 거부한다.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 프런트엔드는 W1의 공개 `/api/v1/*`와 인증 endpoint만 호출하고 W2·W3·W4, SQS, Neo4j 또는 내부 worker endpoint에 직접 접근하지 않아야 한다.
- **FR-002**: 시스템은 Google OpenID Connect Authorization Code Flow를 state, nonce, PKCE와 함께 사용해야 한다.
- **FR-003**: W1은 Google ID token의 서명, issuer, audience, expiry와 nonce를 검증한 후에만 내부 principal을 생성해야 한다.
- **FR-004**: 애플리케이션 세션은 Secure, HttpOnly, SameSite 속성을 가진 불투명 cookie로 전달하고 서버에는 원문이 아닌 세션 식별자 hash를 저장해야 한다.
- **FR-005**: 로그아웃·만료·계정 삭제 시 해당 세션을 폐기하고 프런트 개인 캐시를 제거해야 한다.
- **FR-006**: CORS는 환경별 명시적 frontend origin만 허용하고 credential 사용 시 wildcard를 금지해야 한다.
- **FR-007**: 기존 FastAPI schema와 오류 envelope를 프런트 타입의 정본으로 사용하며 계약 변경은 호환성 검토 없이 수행하지 않아야 한다.
- **FR-008**: 현재 `demo-state.tsx`의 seed/localStorage/타이머 상태 전이를 서버 조회와 mutation으로 교체해야 한다.
- **FR-009**: Activity/Episode, Project/Question, Home/Resume API를 현재 화면 흐름에 연결해야 한다.
- **FR-010**: Job 화면은 `status`, `dispatch_status`, `completeness`, `stage`, `required_actions`, `checkpoint`, `failure`, `limitations`를 서로 다른 의미로 처리해야 한다.
- **FR-011**: 진행 중 Job은 polling하고 terminal·사용자 결정 대기·접근 불가 상태에 맞춰 polling을 중지하거나 완화해야 한다.
- **FR-012**: retry, action, cancel 및 모든 생성·수정 mutation은 중복 클릭에 안전한 멱등성 전략을 사용해야 한다.
- **FR-013**: 추천 run, candidate, material selection을 result/input version과 함께 연결하고 stale 또는 제한 결과의 선택을 막아야 한다.
- **FR-014**: 타인 소유와 미존재 개인 리소스는 같은 404 UX로 처리해야 한다.
- **FR-015**: 서버의 안정적인 오류·reason code로 분기하고 사용자용 문장 내용으로 로직을 분기하지 않아야 한다.
- **FR-016**: 삭제 요청 이후 관련 캐시를 격리하고 늦은 응답으로 삭제 대상이 재노출되지 않게 해야 한다.
- **FR-017**: W1은 프런트에 내부 queue URL, command payload, fence, deletion epoch 원문, Neo4j ID, worker retry 기록을 노출하지 않아야 한다.
- **FR-018**: 합성 추천과 실제 ENGINE 결과를 구분하고, 합성 결과를 실제 엔진 완료로 오표시하지 않아야 한다.
- **FR-019**: 기업 분석 화면에 필요한 요약·근거·공고·상충 자료 중 현재 API에 없는 조회 surface는 기존 SourceVersion/권한 계약을 보존하는 additive API로만 추가해야 한다.
- **FR-020**: 백엔드 기능 변경에는 pytest API/서비스/PostgreSQL 통합 테스트를, 프런트 기능 변경에는 컴포넌트·API adapter·브라우저 E2E 테스트를 추가해야 한다.
- **FR-021**: 운영 로그는 correlation ID, endpoint, 상태, 안전한 오류 코드만 기록하고 token, cookie, OAuth code, 경험·문항·근거 원문을 기록하지 않아야 한다.
- **FR-022**: OpenAPI 기반 프런트 타입 또는 계약 검증을 CI에 포함해 백엔드 응답과 프런트 소비 모델의 drift를 탐지해야 한다.
- **FR-023**: ENGINE 추천 접수는 기존 공개 추천 endpoint와 응답 schema를 유지하고, 실행기 선택은 브라우저 입력이 아닌 W1 배포 설정과 서버 정책으로만 결정해야 한다.
- **FR-024**: W1은 ENGINE Run과 고정된 owner, question version, snapshot, EpisodeVersion, 실행 lease, deletion/currentness revision을 결속하고 W4 호출 중 장기 DB transaction을 유지하지 않아야 한다.
- **FR-025**: ENGINE 실행 지시는 durable outbox/SQS로 전달하고, W4의 context 조회·권한 재검사·결과 게시·실패 반영은 인증된 W1 private HTTP 계약으로 수행해야 한다. Queue 메시지에는 경험 원문이나 모델 비밀을 포함하지 않아야 한다.
- **FR-026**: W1은 W4 후보 상태를 승인된 대응표로 변환하고 상세 결과·Source 의존성·result version을 후보와 같은 원자적 게시 단위로 보존해야 한다.
- **FR-027**: 중복 전달, 응답 유실, worker 재시작, 취소, owner deletion epoch, question/snapshot/Source 변경에서 ENGINE 결과의 논리적 게시 효과는 최대 한 번이어야 하며 stale 결과를 차단해야 한다.
- **FR-028**: ENGINE 실행 실패를 SYNTHETIC 성공으로 자동 대체하지 않아야 하며, `result_origin=ENGINE`은 실제 W4 추천 실행과 W1 게시가 모두 완료된 결과에만 부여해야 한다.
- **FR-029**: 최초 US4.5 운영 검증은 합성 입력으로 제한하고, REAL 사용자 자료 전송은 별도 P2/P3 정책·보존·모델 승인 전까지 비활성화해야 한다.

### Key Entities

- **Authenticated Principal**: 검증된 Google issuer/subject와 내부 owner user의 연결.
- **Auth Session**: 사용자 로그인 세션의 hash, 만료·폐기 상태, 최소 보안 메타데이터.
- **Activity / Episode**: 사용자의 경험과 버전이 있는 세부 사건.
- **Application Project / Question**: 지원 건과 자기소개서 문항 및 immutable version.
- **Job Read Model**: 비동기 처리 상태, dispatch, 완전성, 단계, 사용자 행동, checkpoint, 실패를 합친 W1 공개 조회.
- **Recommendation Run / Candidate**: 고정된 project/question snapshot에 대한 버전 있는 추천 결과와 후보.
- **Recommendation Execution Binding / Publication**: ENGINE Run의 lease·context hash·revision을 고정하고 W4 결과, 상세 본문, Source 의존성을 원자적으로 게시하는 private 실행 단위.
- **Material Selection**: 문항별로 저장된 사용자 소재 선택.
- **Company Evidence Read Model**: 허용된 기업 분석 요약·출처·공고·상충 자료의 공개 projection.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 두 계정 격리 E2E에서 개인 데이터 교차 노출이 0건이고 비인증 개인 API가 모두 401을 반환한다.
- **SC-002**: 사용자가 로그인 후 경험 저장, 프로젝트/문항 생성, 분석 요청, 상태 확인, 후보 선택을 한 브라우저 흐름으로 완료할 수 있다.
- **SC-003**: 네트워크 재시도·중복 클릭 시 동일 사용자 명령으로 중복 Job·선택·리소스가 생성되지 않는다.
- **SC-004**: 테스트된 모든 Job 상태에서 접수·dispatch·실행·결과 사용 가능 상태를 오표시하지 않는다.
- **SC-005**: 삭제 직전 시작된 늦은 응답과 재로그인 이후에도 삭제 대상의 재노출이 0건이다.
- **SC-006**: 프런트 production build, 프런트 unit/component tests, backend pytest, 계약 drift 검사, 핵심 Playwright E2E가 CI에서 통과한다.
- **SC-007**: 브라우저 bundle과 로그 검사에서 client secret, session 원문, 내부 W2~W4 transport 식별자가 0건 노출된다.
- **SC-008**: 합성 고정 입력을 사용한 실제 W4 추천 경로에서 W1 공개 API가 `ENGINE`/`LIMITED` 결과를 반환하고, 정상·중복·응답 유실·재시작·취소·삭제 epoch·revision 변경 검증에서 잘못된 또는 중복 게시가 0건이다.

## Assumptions

- 인증 제공자는 이번 결정으로 Google OIDC를 사용한다. 운영 Client ID/Secret과 허용 도메인은 배포 시 secret/config로 제공한다.
- 현재 Vinext/Next/Vite 화면 구조는 1차 연동 동안 유지하고 대규모 React Router 마이그레이션은 별도 작업으로 분리한다.
- PostgreSQL이 사용자, 세션, 개인 데이터와 Job의 authoritative store다.
- W1의 현재 공개 API와 오류 envelope를 우선 재사용하며, 화면에 부족한 surface만 additive하게 보강한다.
- MVP 상태 갱신은 polling이며 SSE/WebSocket 도입은 이번 범위 밖이다.
- W2~W4 실제 엔진이 준비되지 않은 경로는 명시적인 synthetic fixture/feature flag로 검증하며 운영 완료로 표시하지 않는다.
- 2026-09-20 W1↔W4 CT-12 통과는 Question Core producer→SQS→W1 consumer 경계의 근거이며 W4 추천 publication 경로의 완료 근거로 확대하지 않는다.
- 모바일 네이티브 앱과 Google 외 인증 제공자는 이번 범위 밖이다.
