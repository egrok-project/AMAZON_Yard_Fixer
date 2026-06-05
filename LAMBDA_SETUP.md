# Yard Fixer — AWS Lambda Setup Guide

## Overview

How it works once set up:
1. You upload `origin.csv` to the input bucket
2. You upload `dry_run.csv` to the input bucket
3. Lambda fires automatically
4. `origin_fixed.csv` appears in the output bucket — download and upload to TMS

No Python. No VS Code. No command line.

Two separate buckets are used to prevent Lambda triggering itself in a loop.

---

## What you need before starting

- AWS account (personal or team)
- The following files ready to upload:
  - `yard_fixer_lambda_package.zip` (the Lambda deployment package)
  - `fc_hours.txt`
  - `TerminalHours.csv`
  - `TTH.csv`

---

## Step 1 — Create Two S3 Buckets

Go to **AWS Console → S3 → Create bucket** and create both buckets with identical settings.

**Bucket 1:** `yard-fixer-input`
**Bucket 2:** `yard-fixer-output`

For each bucket:

| Setting | Value |
|---|---|
| Bucket name | as above (must be globally unique — add your name if taken) |
| Region | pick the closest to you, e.g. `eu-west-1` |
| Block all public access | ✅ ON (all four checkboxes) |
| Bucket versioning | Enable |
| Default encryption | SSE-S3 (default — leave it) |

Click **Create bucket**, then repeat for the second bucket.

> Encryption is bucket-level — every file in both buckets is automatically encrypted at rest. No per-folder setup needed.

---

## Step 2 — Enforce HTTPS-Only on Both Buckets (data in transit)

Repeat this for **both** buckets:

1. Go into the bucket → **Permissions** tab → **Bucket policy** → Edit
2. Paste the policy below, replacing the bucket name with the correct one each time:

**For yard-fixer-input:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyHTTP",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::yard-fixer-input",
        "arn:aws:s3:::yard-fixer-input/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    }
  ]
}
```

**For yard-fixer-output:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DenyHTTP",
      "Effect": "Deny",
      "Principal": "*",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::yard-fixer-output",
        "arn:aws:s3:::yard-fixer-output/*"
      ],
      "Condition": {
        "Bool": {
          "aws:SecureTransport": "false"
        }
      }
    }
  ]
}
```

Click **Save changes** after each.

---

## Step 3 — Create Folder Structure

**In yard-fixer-input**, create three folders:
- `input/`
- `reference/`

**In yard-fixer-output**, create one folder:
- `output/`

To create a folder: open the bucket → **Create folder** → type name → Save.

---

## Step 4 — Upload Reference Files

Go into `yard-fixer-input/reference/` and upload:

- `fc_hours.txt`
- `TerminalHours.csv`
- `TTH.csv`

These stay here permanently. Update them in place whenever hours or TT data changes.

---

## Step 5 — Create the Lambda Function

Go to **AWS Console → Lambda → Create function**

| Setting | Value |
|---|---|
| Author from scratch | selected |
| Function name | `YardFixer` |
| Runtime | Python 3.12 |
| Architecture | x86_64 (ARM64 toggle OFF) |
| Execution role | Create a new role with basic Lambda permissions |

Click **Create function**.

---

## Step 6 — Upload the Script

On the function page, under **Code source**:

1. Click **Upload from** → **.zip file**
2. Upload `yard_fixer_lambda_package.zip`
3. Set the handler to: `yard_fixer.lambda_handler`
   - Found under **Runtime settings** → Edit

---

## Step 7 — Set Environment Variables

Under **Configuration** tab → **Environment variables** → Edit → Add both:

| Key | Value |
|---|---|
| `S3_BUCKET` | `yard-fixer-input` |
| `S3_OUTPUT_BUCKET` | `yard-fixer-output` |

Save.

---

## Step 8 — Adjust Timeout and Memory

Under **Configuration** → **General configuration** → Edit:

| Setting | Value |
|---|---|
| Memory | 128 MB (default is fine) |
| Timeout | 1 min 0 sec |

Save.

---

## Step 9 — Grant Lambda Permission to Access Both Buckets

Go to **Configuration** → **Permissions** → click the role name (opens IAM)

In IAM → **Add permissions** → **Create inline policy** → switch to JSON and paste:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::yard-fixer-input/*"
    },
    {
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::yard-fixer-output/*"
    }
  ]
}
```

Name it `YardFixerS3Access` → Create policy.

Read-only on input, write-only on output. Lambda cannot touch anything else.

---

## Step 10 — Set Up the S3 Trigger

On your Lambda function page → **+ Add trigger**

| Setting | Value |
|---|---|
| Source | S3 |
| Bucket | `yard-fixer-input` |
| Event types | PUT |
| Prefix | `input/dry_run` |
| Suffix | `.csv` |

Click **Add**.

Lambda fires automatically when `dry_run.csv` is uploaded to the input bucket.
Always upload `origin.csv` first, then `dry_run.csv` — that's the trigger.

---

## Step 11 — Test It

1. Upload `origin.csv` to `yard-fixer-input/input/`
2. Upload `dry_run.csv` to `yard-fixer-input/input/`
3. Wait ~10–20 seconds
4. Check `yard-fixer-output/output/` — `origin_fixed.csv` should appear
5. Full log: **CloudWatch → Log groups → /aws/lambda/YardFixer**
   - Every FIXED / SKIP / WARN line is recorded there, same as the console output before

---

## Weekly Workflow Going Forward

```
1. Upload origin.csv   →  yard-fixer-input/input/
2. Upload dry_run.csv  →  yard-fixer-input/input/   ← triggers Lambda
3. Wait 10–20 seconds
4. Download origin_fixed.csv  ←  yard-fixer-output/output/
5. Upload to TMS
```

---

## Updating Reference Files

When FC hours, terminal hours, or TT lanes change:

- Go to `yard-fixer-input/reference/` in S3
- Upload the updated file (same name, overwrites the old one)
- Versioning keeps the previous version if you need to roll back

No code changes. No redeployment.

---

## Security Summary

| Concern | How it's handled |
|---|---|
| Data at rest | SSE-S3 encryption on all objects in both buckets (automatic) |
| Data in transit | HTTPS-only bucket policy on both buckets (Step 2) |
| Public access | Blocked entirely on both buckets (Step 1) |
| Lambda permissions | Read-only on input bucket, write-only on output bucket (Step 9) |
| Recursive trigger | Prevented by two-bucket separation — output bucket has no trigger |
| Accidental overwrites | S3 versioning enabled on both buckets (Step 1) |

---

## If Something Goes Wrong

- **Lambda didn't fire:** Check trigger prefix — must be exactly `input/dry_run`
- **Permission error:** Check IAM policy in Step 9 — confirm both bucket names match exactly
- **Output not appearing:** Check CloudWatch logs for the error message
- **Wrong output:** Reference files in `yard-fixer-input/reference/` may be outdated — re-upload them
