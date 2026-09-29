# W1 protected lookup caller

상태: 독립 caller 로컬 구현. 일반 수집 worker·배포·실제 W1 HTTPS 왕복은 별도다.

## API와 주입 경계

`epick_engine.source_collection.w1_lookup_client.W1LookupClient`는 Python 표준 라이브러리의 HTTPS transport를 사용한다. 새 package dependency나 W1 endpoint를 추가하지 않는다.

```python
client = W1LookupClient(
    endpoint=approved_lookup_endpoint,
    bearer=injected_service_credential,
    ssl_context=verified_private_ca_context,
)
response = client.lookup_dispatch(validated_authenticated_dispatch)
```

위 변수는 호출자가 승인된 설정/secret 경로에서 주입하는 값이다. 실제 endpoint·credential·CA 설정을 이 예시나 Git에 넣지 않는다. `ssl_context`를 생략하면 시스템 trust store를 사용하는 verified context를 만든다. 별도 CA context도 `CERT_REQUIRED`와 hostname verification이 필요하다.

- endpoint는 정확히 `https://<approved-host[:port]>/internal/v1/job-commands/lookup` 형태다. 다른 path, HTTP, userinfo, query, fragment, 제어문자는 거부한다.
- 고정 `POST`, `Authorization: Bearer ...`, `X-EPICK-Service-Principal: w2`를 사용한다. Idempotency-Key는 보내지 않는다.
- 요청은 16 KiB, 응답은 64 KiB 제한이다. UTF-8 JSON만 허용하며 중복 key·비유한 숫자·잘못된 wire 형식을 거부한다.
- 기본 socket timeout은 5초이며 유한한 양수만 받는다. 이는 전체 수집 실행 시간이나 G-07 source timeout을 정하는 값이 아니다.
- stdlib transport는 redirect, 환경변수 proxy, 자동 retry를 사용하지 않는다. 연결은 성공·실패 뒤 닫는다.
- 예외와 client repr는 endpoint·bearer·응답 본문을 포함하지 않는다. 호출자가 request/response 객체 또는 traceback의 local variables를 운영 로그에 덤프해서는 안 된다.

`lookup(request)`는 `LookupRequest`를 재검증하여 기존 strict decoder로 응답을 해석한다. `lookup_dispatch(dispatch)`는 I/O 전에 전체 Core/direct dispatch를 재검증하고, AVAILABLE 응답 command 전체가 원본 payload와 같은지 검사한다. QUESTION_MATCHING의 nullable pin company는 Source 수집 command의 Company UUID를 제거한다는 뜻이 아니며, lookup command의 Company 불일치는 계속 거부한다.

## 응답 의미

- HTTP 200의 `AVAILABLE` 외 semantic 상태는 command가 없는 정상 해석 결과다. 이를 실행/재개 성공으로 바꾸지 않는다.
- 401/403/422/503은 기존 W1 error schema/code와 정확히 일치해야 한다. 올바른 503만 `W1LookupError.retryable=True`이지만 caller가 자동 retry하지 않는다.
- 잘못된 JSON·Content-Type·응답 크기·알 수 없는 HTTP status와 transport 실패는 안전한 오류로 종료한다.
- 이 client는 SQS SenderId를 인증하지 않는다. **queue에서 인증된 W1 dispatch + fresh matching AVAILABLE lookup**이 함께 있어야 기존 실행 계약에 해당한다. 임의 생성된 command의 lookup 성공만으로 W1 slot/lease나 commit 권한을 생성하지 않는다.

## 아직 연결되지 않은 범위

실제 Source/정책/robots/실행 한도 provider, 일반 dispatcher/worker CLI, 공용 저장과 private staging의 결합, FINALIZE 후 최신 Source 포인터 승격은 후속 설계·구현 대상이다. 기존 `epick-w2-ct15` entrypoint는 계속 gate-only synthetic 용도이며 이 caller 추가로 일반 worker가 되지 않는다.

W1이 checkpoint 재개에 필요한 policy revision을 보존하지 않는 문제는 별도 수정 대상이다. caller는 그 command를 보정하지 않는다. 실제 endpoint/TLS/bearer 인계, IAM/SQS/registry 및 공동 CT15 증거도 별도다.
