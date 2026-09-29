# Synthetic source fixtures

이 디렉터리의 자료는 EPick W2 계약과 수집기를 검증하기 위한 합성 데이터다. 실제 기업, 실제 채용공고 또는 실시간 수집 결과로 제시하지 않는다.

## 격리 규칙

- 합성 HTTP URL은 `https://<name>.test/...` 형식만 사용한다.
- `synthetic_transport` fixture는 메모리 안의 `httpx.MockTransport`만 만들며 socket 연결을 열지 않는다.
- 등록하지 않은 URL 요청은 즉시 실패하고 응답에 `X-EPICK-Synthetic: true`를 붙인다.
- 기본 pytest 실행은 socket 연결을 차단한다. 운영 SSRF 검사의 loopback·private/metadata 주소 차단을 비활성화하지 않는다.
- 실제 PostgreSQL 검증은 `@pytest.mark.approved_postgres`와 `EPICK_TEST_DATABASE_URL`, `EPICK_TEST_DATABASE_APPROVED=1`을 모두 요구한다. 운영 DSN과 같으면 실패한다.
- W1 principal/dispatch fixture는 계약 경계만 기록하는 test double이다. 인증, Job 상태, Checkpoint, dispatch, 슬롯 또는 fence의 실제 W1 구현 통과를 의미하지 않는다.
- fixture에 credential, 개인 원문, 실제 기업 원문 또는 허가되지 않은 본문을 저장하지 않는다.

각 fixture 파일에는 합성임을 알 수 있는 이름과 설명을 두고, 예상 Source 유형·representation·정책 상태·오류 또는 Evidence 위치를 테스트 안에서 명시한다.

