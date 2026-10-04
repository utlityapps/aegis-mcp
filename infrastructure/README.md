# Infrastructure artifacts

Least-privilege IAM policies for the AWS deployment described in `docs/aws_bedrock_integration.md`. **They aren't deployed or tested against AWS.** The JSON is syntax-checked only.

| File | Attach to | Grants |
|---|---|---|
| `iam_bedrock_policy.json` | The role of the Strands agent process | `InvokeModel` and `InvokeModelWithResponseStream` on **one** foundation model, in one region. Explicitly denies creating, changing or deleting Bedrock resources. |
| `iam_cloudwatch_policy.json` | The role of whatever ships the log files (CloudWatch agent or Fluent Bit) | Create streams and put events in `/aegis/server` and `/aegis/metrics` only. Explicitly denies deleting log groups or streams, changing retention, and reading logs. |

**No `cloudwatch:PutMetricData`.** EMF metrics are extracted by CloudWatch Logs from the log lines themselves, so the server never calls the metrics API.

## Fill in the placeholders

IAM JSON has no comments or variables. The placeholders are `__LIKE_THIS__` so they can't be mistaken for IAM's own `${aws:...}` policy variables. Render them before use:

```bash
sed -e "s/__AWS_REGION__/us-east-1/g" \
    -e "s/__AWS_ACCOUNT_ID__/123456789012/g" \
    -e "s/__BEDROCK_MODEL_ID__/<model id enabled in your account>/g" \
    iam_bedrock_policy.json > rendered/iam_bedrock_policy.json
```

- **Cross-region inference profiles** (model IDs starting with a geography prefix such as `us.`) need the profile ARN, `arn:aws:bedrock:<region>:<account>:inference-profile/<profile id>`, **and** the foundation-model ARN in every region the profile routes to. Widen `Resource` to exactly those ARNs, and drop the `aws:RequestedRegion` condition if it blocks the routed regions.
- Create both log groups ahead of time, with a retention period. The policy deliberately doesn't allow `logs:CreateLogGroup`.

## Getting EMF metrics into CloudWatch

The server writes one EMF JSON document per line to **stdout** (`aegis.metrics`) and human-readable, PII-redacted logs to **stderr**.
- **AWS Lambda** extracts EMF from stdout natively.
- **Anywhere else** (EC2, plain containers), something has to ship the lines to the `/aegis/metrics` log group as EMF. CloudWatch only extracts metrics when the events arrive flagged as EMF (the `x-amzn-logs-format: json/emf` header on `PutLogEvents`). Use a shipper that sets it, for example Fluent Bit's `cloudwatch_logs` output with `log_format json/emf`, running under the role above.

So "no agent" holds only on Lambda. On EC2, plan for a log shipper.

With systemd (see `docs/aws_bedrock_integration.md` §5.2), split the two streams into files for the shipper:

```ini
StandardOutput=append:/var/log/aegis/metrics.log
StandardError=append:/var/log/aegis/server.log
```
