# Lambda Anomaly Analysis — Deployment Guide

## Overview

Runs the V10 research anomaly analysis as an AWS Lambda function.  
Reads validated trade data from S3, classifies anomalies, uploads reports back to S3.

## Package Structure

```
lambda/anomaly_analysis/
├── lambda_function.py      # Lambda entry point
├── research_anomaly.py     # Analysis logic (extracted from core/)
├── config.py               # Environment-based configuration
└── requirements.txt        # Dependencies (boto3 only)
```

## Required IAM Permissions

The Lambda execution role needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::v10-engine/research_ready_trade_dataset/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::v10-engine/reports/research/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "*"
    }
  ]
}
```

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `S3_BUCKET` | `v10-engine` | S3 bucket name |
| `DATASET_PATH` | `research_ready_trade_dataset/research_ready_trades.jsonl` | Input data key |
| `REPORT_PATH` | `reports/research/` | Output prefix for reports |
| `EXTREME_R_HIGH` | `5.0` | R-multiple threshold for flagging |
| `EXTREME_R_LOW` | `-3.0` | Negative R threshold |
| `EXTREME_PNL_PERCENTILE` | `2.5` | PnL percentile for extreme detection |

## Deployment Steps

### 1. Create deployment package

```bash
cd lambda/anomaly_analysis
pip install -r requirements.txt -t .
zip -r anomaly_analysis.zip . -x "*.pyc" -x "__pycache__/*" -x "output/*"
```

Note: `boto3` is pre-installed in the Lambda runtime. The requirements.txt is for local testing only. You can skip packaging boto3 to reduce zip size.

### 2. Create Lambda function (AWS Console or CLI)

```bash
aws lambda create-function \
  --function-name v10-anomaly-analysis \
  --runtime python3.11 \
  --handler lambda_function.lambda_handler \
  --role arn:aws:iam::ACCOUNT_ID:role/v10-lambda-research-role \
  --zip-file fileb://anomaly_analysis.zip \
  --timeout 60 \
  --memory-size 256 \
  --environment Variables="{S3_BUCKET=v10-engine,DATASET_PATH=research_ready_trade_dataset/research_ready_trades.jsonl,REPORT_PATH=reports/research/}"
```

### 3. (Optional) Schedule with EventBridge

Run daily at 06:00 UTC:

```bash
aws events put-rule \
  --name v10-anomaly-daily \
  --schedule-expression "cron(0 6 * * ? *)"

aws events put-targets \
  --rule v10-anomaly-daily \
  --targets "Id"="1","Arn"="arn:aws:lambda:REGION:ACCOUNT_ID:function:v10-anomaly-analysis"
```

## Local Testing

### Option 1: Local filesystem (no AWS credentials needed)

```bash
cd lambda/anomaly_analysis
python lambda_function.py --local
```

This reads from `../../logs/research_ready_trade_dataset/` and writes to `./output/`.

### Option 2: Against real S3 (requires AWS credentials)

```bash
cd lambda/anomaly_analysis
export S3_BUCKET=v10-engine
export AWS_PROFILE=your-profile
python lambda_function.py
```

## Lambda Configuration

| Setting | Recommended |
|---|---|
| Runtime | Python 3.11 |
| Memory | 256 MB |
| Timeout | 60 seconds |
| Architecture | x86_64 |

## Monitoring

The function logs to CloudWatch with prefix `[ANOMALY_LAMBDA]`.

Key log lines:
- `Invoked at` — execution start
- `Loaded X trades` — dataset read confirmation
- `Analysis complete: X trades, Y anomalies` — processing result
- `Uploaded s3://` — report write confirmation
- `Complete in Xms` — total execution time

## Updating

To update the function code:

```bash
cd lambda/anomaly_analysis
zip -r anomaly_analysis.zip lambda_function.py research_anomaly.py config.py
aws lambda update-function-code \
  --function-name v10-anomaly-analysis \
  --zip-file fileb://anomaly_analysis.zip
```
