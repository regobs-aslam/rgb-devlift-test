# hartest_1

Small Flask test app used to exercise the runtime IAM permissions granted to a
pod via EKS Pod Identity. Credentials come from Pod Identity — no access keys
are configured or needed.

## Test endpoints

| Method | Path            | What it proves                                                                                                               |
| ------ | --------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/health`       | Process is alive.                                                                                                            |
| GET    | `/test/secrets` | `secretsmanager:DescribeSecret` + `GetSecretValue` on `$SECRET_NAME_OR_ARN`. Returns key names only — never secret values.   |
| POST   | `/test/dynamo`  | `PutItem` + `GetItem` on `$DYNAMODB_TABLE` (round-trip a `probe-<uuid>` row).                                                |
| POST   | `/test/s3`      | `PutObject` + `GetObject` + `DeleteObject` on `$S3_BUCKET` (round-trip a `probe-<uuid>.txt`).                                |
| POST   | `/test/sqs`     | `SendMessage` + `ReceiveMessage` + `DeleteMessage` on `$SQS_QUEUE_URL`.                                                      |

Every endpoint returns HTTP 500 on failure with
`{"ok": false, "error": "...", "error_type": "AccessDenied" | "ResourceNotFound" | ...}`
so you can distinguish IAM failures from missing resources.

## Required env vars

| Variable                  | Required | Default     | Purpose                                                                 |
| ------------------------- | -------- | ----------- | ----------------------------------------------------------------------- |
| `AWS_REGION`              | no       | `us-east-1` | Region for all AWS SDK clients.                                         |
| `SECRET_NAME_OR_ARN`      | yes\*    | —           | Name or ARN of the Secrets Manager secret to probe (`/test/secrets`).   |
| `DYNAMODB_TABLE`          | yes\*    | —           | DynamoDB table name used by `/test/dynamo`.                             |
| `DYNAMODB_PARTITION_KEY`  | no       | `pk`        | Partition key attribute name for `$DYNAMODB_TABLE`.                     |
| `S3_BUCKET`               | yes\*    | —           | S3 bucket used by `/test/s3`.                                           |
| `SQS_QUEUE_URL`           | yes\*    | —           | Full queue URL used by `/test/sqs`.                                     |

\* Required only for the matching endpoint. Missing values return 500 with
`error_type: ConfigError`.

Pod Identity note: the pod's ServiceAccount is associated with an IAM role — the
AWS SDK picks up credentials automatically from the Pod Identity agent. Do not
inject access keys.
