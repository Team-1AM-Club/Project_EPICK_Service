# 합성 정상 사례와 기대 결과

**성격**: 설계를 설명하는 합성 원문·계약 조각이다. 실제 기업 자료나 실행된 테스트 결과가 아니다.
**관련 요구**: FR-005~FR-011, FR-020; AS-201, AS-202, AS-301, AS-306.

아래 자료는 이 계획에서 새로 만든 가상 입력이다.
각 SourceVersion은 합성 자료 제작자가 명시적으로 발급한 테스트 ID이며 실제 샘플의 누락 버전을 보정한 값이 아니다.
원문·근거 조각은 [data-model.md](../data-model.md)의 표현을 설명하며 완전한 StructureRequest JSON은 아니다.
실제 fixture·추출기·검증기·테스트 구현은 후속 단계에서 만든다.

## 보존 원문과 위치·해시

아래 해시는 문서 작성 때 각 문자열의 정확한 UTF-8 바이트로 계산했다.
개행과 BOM을 추가하지 않았다. Evidence는 예시 문자열 전체를 가리킨다.
가상 문서 원본 위치는 `synthetic-paragraph:1`이며 제작자가 버전과 위치를 함께 정의한다.
실제 W2 locator 형식이 확정되었다는 의미는 아니다.

### SYN-PLAN

```json
{
  "example_kind": "SYNTHETIC",
  "source_id": "SYN-PLAN",
  "source_version_id": "SYN-PLAN-v1",
  "text_artifact_id": "SYN-PLAN-paragraph-1",
  "retention_scope": "EXCERPT",
  "text": "가상기업 가온의 연구팀은 2027년 상반기에 합성 검증 도구를 도입할 계획이다.",
  "integrity": {
    "algorithm": "sha256",
    "encoding": "utf-8",
    "target_scope": "exact_text",
    "expected_digest": "cfa096abc21a7a03f12a6e8854ac4096dd95fa22ed71c5426f07e732c106ea8e"
  },
  "evidence": {
    "start": 0,
    "end": 44,
    "exact_quote": "가상기업 가온의 연구팀은 2027년 상반기에 합성 검증 도구를 도입할 계획이다."
  }
}
```

### SYN-OR

```json
{
  "example_kind": "SYNTHETIC",
  "source_id": "SYN-OR",
  "source_version_id": "SYN-OR-v1",
  "text_artifact_id": "SYN-OR-paragraph-1",
  "retention_scope": "EXCERPT",
  "text": "AWS 또는 GCP를 사용한 서비스의 운영 경험 필수",
  "integrity": {
    "algorithm": "sha256",
    "encoding": "utf-8",
    "target_scope": "exact_text",
    "expected_digest": "342e50b2c000be1035f40a72e9c3341c3043e761ac6a857a4451574a05c39ae6"
  },
  "evidence": {
    "start": 0,
    "end": 29,
    "exact_quote": "AWS 또는 GCP를 사용한 서비스의 운영 경험 필수"
  }
}
```

### SYN-EXCEPT

```json
{
  "example_kind": "SYNTHETIC",
  "source_id": "SYN-EXCEPT",
  "source_version_id": "SYN-EXCEPT-v1",
  "text_artifact_id": "SYN-EXCEPT-paragraph-1",
  "retention_scope": "EXCERPT",
  "text": "운영 경험 3개월 이상 필수. 단, 교육용 실습은 제외",
  "integrity": {
    "algorithm": "sha256",
    "encoding": "utf-8",
    "target_scope": "exact_text",
    "expected_digest": "d31e4ad45bd02d09c81a97db27b00f231e9dab89529d09a67a4abcfe0efe2f67"
  },
  "evidence": {
    "start": 0,
    "end": 30,
    "exact_quote": "운영 경험 3개월 이상 필수. 단, 교육용 실습은 제외"
  }
}
```

## 기대 결과와 금지 결과

| 자료 | 미리 정한 정상 기대값 | 실패로 판단할 변형 |
|---|---|---|
| SYN-PLAN | Claim 1개: PLAN, 가상기업 가온 연구팀, 합성 검증 도구 도입 계획, 유효 기간 2027년 상반기, 원문 Evidence 연결 | 도입 완료·성과 개선·없는 기술/수치/채용 요구 추가 |
| SYN-OR | Requirement 1개: REQUIRED, AWS/GCP 대안과 같은 서비스 운영 경험의 결합 | AWS와 GCP를 AND로 변경, 별개 경험으로 분해, AWS=GCP 동의어 처리 |
| SYN-EXCEPT | Requirement 1개: REQUIRED, 운영 경험 GTE 3개월, 교육용 실습 제외 | 3년으로 변경, 비교를 초과로 변경, 제외 조건 삭제 |

이 세 자료를 묶는 기본 정상 fixture는 **일반 Claim 1개·Requirement 2개**를 기대한다.
요구 진술을 동일 내용의 일반 Claim으로 한 번 더 복사하지 않는다.
각 자료의 적용 공고·직무 범위는 fixture가 명시적으로 주고 다른 회사·직무로 확장하지 않는다.
실제 모델의 출력문을 사후에 기대값으로 바꾸지 않고 사전 정답과 의미 성분을 비교한다.

SYN-OR의 조건 트리 예시는 다음과 같다. 아래 `ev-or`는 SYN-OR의 전체 인용을 가리킨다.

```json
{
  "root_node_id": "same",
  "nodes": [
    {"node_id": "same", "operator": "SAME_EPISODE", "child_ids": ["both"], "raw_expression": "서비스의 운영 경험", "evidence_refs": ["ev-or"], "atom": null},
    {"node_id": "both", "operator": "AND", "child_ids": ["cloud", "operation"], "raw_expression": "AWS 또는 GCP를 사용한 서비스의 운영 경험", "evidence_refs": ["ev-or"], "atom": null},
    {"node_id": "cloud", "operator": "OR", "child_ids": ["aws", "gcp"], "raw_expression": "AWS 또는 GCP", "evidence_refs": ["ev-or"], "atom": null},
    {"node_id": "aws", "operator": "ATOM", "child_ids": [], "raw_expression": "AWS", "evidence_refs": ["ev-or"], "atom": {"raw_condition": "AWS 사용"}},
    {"node_id": "gcp", "operator": "ATOM", "child_ids": [], "raw_expression": "GCP", "evidence_refs": ["ev-or"], "atom": {"raw_condition": "GCP 사용"}},
    {"node_id": "operation", "operator": "ATOM", "child_ids": [], "raw_expression": "운영 경험", "evidence_refs": ["ev-or"], "atom": {"raw_condition": "해당 서비스 운영 경험"}}
  ]
}
```

raw_expression은 원문에서 찾을 수 있는 표현이고 atom은 이를 구조화한 성분이다.
atom의 표현도 의미 검증을 통과해야 하며 원문에 없는 조건을 추가할 수 없다.

## 정상 fixture의 신뢰 문맥

테스트 harness는 별도 SYNTHETIC_TEST 문맥에서 해당 가상 Source의 읽기·처리·발췌 보존·공용 결과 투영을 허용한다.
의미 검증과 필요한 추가 검사 대역은 사전 정의한 판정을 반환하고 모두 fixture 방식임을 기록한다.
이 허용과 판정은 가상 자료에만 적용되며 LIVE 환경의 정책 기본값이 아니다.
검증된 것으로 보이는 테스트 결과도 evaluation_mode 때문에 실제 추천 경로가 거부해야 한다.

부정 fixture에서는 정상 원문과 정책을 유지한 채 후보의 유형·숫자·AND/OR·예외만 변형한다.
추출 후보와 기대 결과를 동시에 바꾸지 않는다.
별도로 잘못된 SourceVersion·위치·기대 digest, 검증 정책 누락·판정 미완료, 반환 직전 권한 철회를 주입한다.
검증 상태와 활용 허용 상태가 각각 기대한 이유로 제한되는지 확인한다.
