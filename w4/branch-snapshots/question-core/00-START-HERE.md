> 최신 상태: [실제 CT-12 결과 수용·Compose 보정 r6](docs/w4-ct12-result-acceptance-2026-09-20.md). 실제 W4→W1 PASS는 W1 보고 기준이며 W2 확인·staging teardown은 대기 중입니다.

# W4 Question Core runtime source · 2026-09-20 r5

[W1 인계 회신](docs/w4-http-runtime-handoff-2026-09-20.md) · [실행 안내](docs/w4-http-runtime-deployment-2026-09-20.md).

W1 HTTP context, producer, durable SQLite outbox, send-only SQS relay, 합성 CT-12 plan과 컨테이너 구성입니다.
이전 미커밋 W4 소스/테스트·계약 근거도 포함하여 실행과 전체 회귀 검증을 재현할 수 있게 했습니다.
현재 checkout의 `git rev-parse HEAD`로 full SHA를 확인하고 해당 SHA의 GitHub Actions 결과를 대조하세요.

CI/로컬 통과는 실제 W1 접속, AWS SQS 송신, ECR 배포 또는 공동 CT-12의 완료를 뜻하지 않습니다.
W1 owner deletion epoch 검사 누락은 인계 회신 C1을 확인하세요. 실제 전송 전 W1 수정/DB 검증이 필요합니다.
P2/P3 운영 승인 대기, REAL 발행 DISABLED, 실제 queue/identity는 W1 provisioning 이후 안전 채널로 전달합니다.

`output/`의 예전 모델 비교 자료와 `VERIFICATION.json`의 historical_baseline은 이전 단계의 이력입니다.
새 런타임 검증은 현재 SHA에 연결된 CI artifact 및 별도 전달 ZIP의 evidence/에서 확인하세요.
