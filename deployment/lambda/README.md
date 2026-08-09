# V10 Research Lambda — Deployment Guide

## Overview

The V10 Research Lambda executes research operations against data stored in S3.
It does NOT connect to MT5, place trades, or modify live bot configuration.

```
S3 (research data) → Lambda → Research Engine → Reports → S3
```

---

## Prerequisites

- AWS CLI configured with appropriate credentials
- Python 3.11+
- S3 bucket for research data (default: `v10-engine`)

---

## Step-by-Step Deployment

### 1. Build the package

```bash
python tools/build_lambda_package.py
```

Output: `build/v10-research-lambda.zip` (~158 KB)

### 2. Validate the package

```bash
python tools/validate_lambda_package.py
```

Confirm: all checks PASS.

### 3. Create the S3 bucket (if not exists)

```bash
aws s3 mb s3://v10-engine --region ap-southeast-2
```

### 4. Upload research data to S3

```bash
aws s3 cp data/research/research_universe.jsonl s3://v10-engine/data/research/research_universe.jsonl
```

### 5. Create the Lambda function

```bash
aws lambda create-function \
  --function-name v10-research-engine \
  --runtime python3.11 \
  --handler lambda_handler.handler \
  --zip-file fileb://build/v10-research-lambda.zip \
  --role arn:aws:iam::ACCOUNT_ID:role/v10-research-lambda-role \
  --timeout 300 \
  --memory-size 512 \
  --environment "Variables={RESEARCH_STORAGE=s3,RESEARCH_BUCKET=v10-engine,RESEARCH_UNIVERSE_KEY=data/research/research_universe.jsonl,RESEARCH_REPORT_PREFIX=reports/research/,RESEARCH_STATE_KEY=research/operations/state.json}"
```

### 6. Create IAM Role

The Lambda role needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::v10-engine",
        "arn:aws:s3:::v10-engine/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "logs:*",
      "Resource": "arn:aws:logs:*:*:*"
    }
  ]
}
```

The role must NOT have: EC2, MT5, trading, or administrator permissions.

### 7. Test: run_question

```bash
aws lambda invoke \
  --function-name v10-research-engine \
  --payload '{"action":"run_question","question_id":"E1"}' \
  response.json

cat response.json
```

### 8. Test: run_campaign

```bash
aws lambda invoke \
  --function-name v10-research-engine \
  --payload '{"action":"run_campaign","campaign_id":"FX_OPT_V1"}' \
  response.json
```

### 9. Test: generate_report

```bash
aws lambda invoke \
  --function-name v10-research-engine \
  --payload '{"action":"generate_report"}' \
  response.json
```

Verify report appears in S3:
```bash
aws s3 ls s3://v10-engine/reports/research/
```

---

## Update Lambda Code

```bash
python tools/build_lambda_package.py
aws lambda update-function-code \
  --function-name v10-research-engine \
  --zip-file fileb://build/v10-research-lambda.zip
```

---

## SAM Deployment (Alternative)

```bash
sam build --template deployment/lambda/template.yaml
sam deploy --guided
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| RESEARCH_STORAGE | Backend: `s3` or `local` | `local` |
| RESEARCH_BUCKET | S3 bucket name | `v10-engine` |
| RESEARCH_UNIVERSE_KEY | Universe file key | `data/research/research_universe.jsonl` |
| RESEARCH_REPORT_PREFIX | Report output prefix | `reports/research/` |
| RESEARCH_STATE_KEY | State file key | `research/operations/state.json` |

---

## What Kiro Prepares

- `build/v10-research-lambda.zip` — deployment package
- `lambda_requirements.txt` — dependencies (empty: stdlib only)
- `tools/validate_lambda_package.py` — package validation
- `tools/test_lambda_local.py` — local execution test

## What You Must Do In AWS

- Create S3 bucket
- Create IAM role with least-privilege S3 access
- Create Lambda function
- Upload research data to S3
- Configure environment variables
- Test invocations

---

## Safety

The Lambda:
- Cannot connect to MT5
- Cannot place orders
- Cannot modify live bot
- Has no broker credentials
- Only reads/writes S3 research data
