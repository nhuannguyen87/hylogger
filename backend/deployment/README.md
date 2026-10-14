# Deploy the read-only API

Amplify builds `frontend`; it does not run the Python API. `lambda.yaml` supplies
a Python 3.12 x86_64 Lambda and HTTPS Function URL for an **existing** Aurora
PostgreSQL Express gateway and private S3 bucket. It does not create or modify
database tables, database grants, bucket policies or network access.

## Review before applying

The stack creates a Lambda execution role limited to:

- `rds-db:connect` for the selected cluster resource ID and `etl4_reader`;
- `s3:GetObject` under the selected bucket/prefix;
- writes to this function's CloudWatch log group (7-day retention).

The API reads `core.active_release`; do not hard-code the historical five-hole
release. Obtain `DataRootKey` and `DataPrefix` from current verified S3 asset
registrations. Older handoff `target.json` files may refer to an obsolete prefix.
IAM DB tokens stay server-side and require `verify-full` TLS. The Express gateway
uses a trusted public CA. Ordinary private Aurora clusters need suitable
networking and CA configuration; this template is not that setup.

`PublicAccess` defaults to `false` (AWS IAM authenticated URL). Setting it to
`true` permits anyone with the URL to read active-release boreholes, metadata,
sample results, spectra and images. CORS limits browser origins; it is **not
authentication** and does not prevent direct HTTP access. Public exposure and
the new IAM role require the account owner's explicit approval. Lambda, S3,
CloudWatch and Aurora usage may incur charges; this template has no spending
cap. Check concurrency quotas: new accounts may allow only 10 concurrent
executions. This template does not reserve concurrency because AWS requires
100 executions to remain unreserved.

## Build and validate

From `backend`, using Python 3.12 or newer:

```sh
python -m pip install -r requirements.txt -r requirements-lambda.txt
python -m pytest tests/test_contract_offline.py -q
python deployment/build_lambda.py --output /tmp/hylogger-api.zip
python deployment/check_live.py
```

The builder resolves Linux wheels for Lambda Python 3.12 even on Windows. It
preserves native libraries, metadata and licenses, removes package tests/bytecode,
and rejects ZIPs at or above 250 MiB uncompressed. It emits a SHA-256 manifest.
Optional `--wheel-cache PATH --offline` resolves only from an existing cache.
The roughly 86 MiB compressed ZIP must be uploaded through S3.

`check_live.py` only reads data and accepts the active release dynamically. It
checks stable source fixtures, pagination and equal-depth identities, and fails
if those fixtures have been deliberately removed. Health checks database and
configuration; the smoke check actually reads PNG/spectral assets. Local success
does not prove a Lambda runtime has been deployed.

## Apply after approval

1. Upload the ZIP to a private deployment prefix in a same-region S3 bucket.
   Use its SHA-256 in the key. Keep it outside the data-read prefix.
2. Validate `lambda.yaml` with CloudFormation. Create a change set with reviewed
   parameters and `CAPABILITY_IAM`; inspect it before execution.
3. Supply CodeBucket/CodeKey, DataBucket/DataPrefix/DataRootKey, DatabaseHost,
   DatabaseResourceId, DatabaseReader, DatabaseName and AllowedOrigin. Set
   PublicAccess=true only for an approved public API. Both URL-invocation
   permissions required for new Lambda URLs are included.
4. Wait for successful creation. Run the smoke check against `ApiOrigin`, then
   verify GET/OPTIONS CORS from the exact Amplify origin.
5. Set Amplify `NEXT_PUBLIC_API_BASE` to `ApiOrigin` and rebuild main. Check the
   website with real data in Edge, including Compare and missing trajectories.

To withdraw public access, update PublicAccess=false, rebuild the frontend with
the API variable removed, and verify unsigned calls are rejected. Stack deletion
removes its Lambda/URL/role/logs, not the existing database or source bucket; log
deletion is permanent. Keep the prior Git commit and deployment ZIP for rollback.

AWS references: [ZIP limits](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html),
[URL authorization](https://docs.aws.amazon.com/lambda/latest/dg/urls-auth.html),
[concurrency](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html).
