# HyLogger Explorer Backend

FastAPI backend for the GSWA/NVCL HyLogger visualisation platform.

The backend provides read-only access to the fixed ETL4 HyLogger release using PostgreSQL/PostGIS together with verified Parquet, spectral, and image assets.

## Current Status

The backend is connected to the real ETL4 handoff dataset and has been validated against both the local handoff and the AWS Aurora/S3 deployment.

Validated release:

```text
fd02659c-dc3d-52ea-a8eb-c032b91e7624
```

Current dataset:

- 5 drillholes
- 261,892 samples
- PostgreSQL/PostGIS metadata and spatial data
- mineral/scalar Parquet data
- profile Parquet data
- VSWIR and TIR spectra
- drill-core image assets
- tray/section intervals
- quality and provenance records

The application uses the read-only PostgreSQL role:

```text
etl4_reader
```

## Features

- Drillhole metadata and GeoJSON
- Bounding-box map queries
- Nearby-hole PostGIS search
- Dataset and log discovery
- Paginated sample retrieval
- Mineral/scalar depth tracks
- VSWIR and TIR spectra
- Profile-array retrieval
- Drill-core image mapping and image delivery
- Tray and section intervals
- Quality issue reporting
- Dependency-aware health checks
- Automated integration tests

## Sample Identity

Measured depth is not a unique sample identifier.

Samples are identified using:

```text
release_id
dataset_id
axis_id
sample_no
```

Multiple samples may share the same measured depth.

## Configuration

### Local development

Create a `.env` file in the backend project directory:

```env
ETL4_READ_DSN=host=localhost port=5432 dbname=etl4_core user=etl4_reader password=YOUR_PASSWORD sslmode=disable
ETL4_DB_AUTH=password
ETL4_ASSET_BACKEND=local
ETL4_ROOT=C:\path\to\ETL4_HANDOFF\etl4
```

`.env` is excluded from Git.

### AWS Aurora and S3

AWS mode uses temporary AWS credentials and Aurora IAM database authentication. No PostgreSQL password is stored in the DSN.

```env
ETL4_READ_DSN=host=YOUR_AURORA_HOST port=5432 dbname=etl4_core user=etl4_reader
ETL4_DB_AUTH=iam
ETL4_AWS_REGION=ap-southeast-2

ETL4_ASSET_BACKEND=s3
ETL4_S3_BUCKET=YOUR_BUCKET
ETL4_S3_PREFIX=etl4/releases/fd02659c-dc3d-52ea-a8eb-c032b91e7624
ETL4_S3_ROOT_KEY=etl4_s3_primary
```

## Install

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

```powershell
python -m uvicorn app.main:app --reload
```

Swagger documentation:

```text
http://127.0.0.1:8000/docs
```

## Main API Routes

```text
GET /api/health

GET /v1/boreholes
GET /v1/boreholes/nearby
GET /v1/boreholes/{hole_id}/datasets

GET /v1/datasets/{revision_id}/logs
GET /v1/datasets/{revision_id}/samples
GET /v1/datasets/{revision_id}/samples/{sample_no}

GET /v1/datasets/{revision_id}/logs/{log_id}/values
GET /v1/datasets/{revision_id}/profile-logs/{log_id}/values

GET /v1/datasets/{revision_id}/intervals
GET /v1/datasets/{revision_id}/anomalies
GET /v1/datasets/{revision_id}/issues

GET /v1/image-assets/{asset_id}/content
```

The exact sample endpoint can return scalar/mineral results, spectra, profile results, and mapped image information.

## Spectral Data

Verified examples include:

```text
VSWIR Reflectance: 531 channels
TIR Base Refl:     341 channels
```

Unavailable spectral payloads remain explicitly unavailable or metadata-only. Missing data is not fabricated.

## Profile Data

Profile logs are returned as:

```text
available_uncalibrated_profile
```

The current profile arrays contain 64 values per sample.

No physical coordinate geometry is invented for those values.

## Tests

Run:

```powershell
python -m pytest -v
```

Current status:

```text
24 tests passing
```

The test suite covers real database access, drillholes, dataset/log discovery, samples, mineral values, spectra, profiles, images, intervals, anomaly intervals, quality issues, and PostGIS queries.

## Architecture

```text
PostgreSQL/PostGIS
        |
        v
FastAPI
        |
        +-- Parquet mineral/scalar data
        +-- Parquet profile data
        +-- spectral binary assets
        +-- PNG/JPG image assets
        |
        v
JSON / GeoJSON / Image API
        |
        v
Frontend
```

## Production Deployment

The backend supports both local development and the deployed AWS ETL4 environment.

The AWS deployment uses:

```text
Amazon Aurora PostgreSQL/PostGIS
Private Amazon S3 storage
IAM database authentication
Temporary AWS credentials
```

The public FastAPI contract should remain independent of the underlying storage backend.

## Security

- Dedicated `etl4_reader` read-only database role
- No application database writes
- Credentials kept outside source control
- Asset paths restricted to the configured ETL4 root
- Image assets validated against the active release

## Project Responsibility

This repository implements the Task 3 backend layer.

Raw TSG/BIP parsing and ETL belong to the data-pipeline task.

Frontend rendering is handled separately.
