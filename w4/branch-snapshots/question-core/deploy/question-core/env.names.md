# W4 배포 설정 이름

값을 이 파일이나 저장소에 쓰지 않는다. W1이 안전 채널로 주입한다.

Compose 변수: `W4_IMAGE_REF` (ECR `@sha256:` 필수), `W4_RUNTIME_ENV_FILE`,
`W4_CT12_PLAN_DIR`, `W4_AWS_SESSION_DIR`.

W4 전용 env 파일: `W1_W4_CONTEXT_BEARER`, `W4_SQS_MAIN_QUEUE_URL`,
`W4_SQS_MAIN_QUEUE_ARN`, `W4_AWS_REGION`, `W4_SQS_SEND_ENABLED`.
전송은 정확히 `true`일 때만 켜진다. 기본은 꺼짐이다.

Compose 고정값: `W1_W4_CONTEXT_BASE_URL`, `W1_W4_CONTEXT_PRINCIPAL`,
`W4_CT12_PLAN_PATH`, `W4_AWS_SESSION_PATH`, `W4_OUTBOX_PATH`, `AWS_EC2_METADATA_DISABLED`.

W1 DB DSN, W1 worker env, W1 EC2 자격증명, W2 bearer를 넣지 않는다.
전용 role의 AssumeRole은 W1 호스트가 수행한다. W4 컨테이너에는 그 결과만 넣는다.
자세한 회전·파일 권한·IMDS 네트워크 차단은 실행 안내를 따른다.
