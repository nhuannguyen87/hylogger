# Frontend API Handoff
FastAPI backend for the HyLogger frontend.
## Base URL
Local development:
```text
http://127.0.0.1:8000
```
Interactive API docs:
```text
http://127.0.0.1:8000/docs
```
---
## 1. Health Check
```http
GET /api/health
```
Example response:
```json
{
   "status": "ok",
   "service": "HyLogger Explorer API",
   "database": "etl4_core",
   "database_role": "etl4_reader",
   "release_id": "fd02659c-dc3d-52ea-a8eb-c032b91e7624",
   "asset_root_available": true
}
```
---
## 2. Borehole List
```http
GET /v1/boreholes
```
Optional map bounding box:
```http
GET /v1/boreholes?bbox=west,south,east,north
```
Example:
```http
GET /v1/boreholes?bbox=118.4,-27.3,118.7,-27.0
```
Use this endpoint for:
- map markers
- borehole list
- map filtering
---
## 3. Nearby Boreholes
```http
GET /v1/boreholes/nearby
```
Parameters:
```text
latitude
longitude
radius_km
limit
```
Example:
```http
GET /v1/boreholes/nearby?latitude=-27.13891542&longitude=118.51447554&radius_km=50&limit=20
```
Response items contain:
```text
hole_id
latitude
longitude
distance_km
```
Use this for the "nearest holes" UI.
---
## 4. Borehole Datasets
```http
GET /v1/boreholes/{hole_id}/datasets
```
Example:
```http
GET /v1/boreholes/07THD002/datasets
```
This returns the dataset revision and sample axis needed for later requests.
Important frontend fields:
```text
dataset_revision_id
axis_id
sample_count
```
---
## 5. Dataset Logs
```http
GET /v1/datasets/{revision_id}/logs
```
Example:
```http
GET /v1/datasets/ca3660de-f678-5256-89f5-40d17832e4eb/logs
```
Logs may include:
```text
scalar
spectral
profile
image
```
Useful fields include:
```text
log_id
source_log_name
log_kind
availability_status
spectral_region
axis_binding_status
```
Do not assume every log has data.
Possible states include:
```text
payload_present
metadata_only
```
---
## 6. Samples
```http
GET /v1/datasets/{revision_id}/samples
```
Required:
```text
axis_id
```
Optional:
```text
from_m
to_m
after_sample
limit
```
Example:
```http
GET /v1/datasets/ca3660de-f678-5256-89f5-40d17832e4eb/samples?axis_id=b9ba28cd-0216-5314-83df-f6247b0f56b6&from_m=62.5&to_m=63.0&limit=100
```
Important:
```text
sample_no
md_m
```
Sample identity must use `sample_no`.
Do not use depth alone as a unique identifier because multiple samples may have the same depth.
---
## 7. Exact Sample
```http
GET /v1/datasets/{revision_id}/samples/{sample_no}
```
Required:
```text
axis_id
```
Optional:
```text
log_ids
image_log_id
```
Example mineral request:
```http
GET /v1/datasets/ca3660de-f678-5256-89f5-40d17832e4eb/samples/15?axis_id=b9ba28cd-0216-5314-83df-f6247b0f56b6&log_ids=a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8
```
Known example:
```text
hole: 07THD002
sample_no: 15
depth: 62.50175 m
mineral: Muscovite
```
The response can include:
```text
sample information
scalar/mineral results
spectral results
profile results
image information
```
---
## 8. Mineral / Scalar Depth Track
```http
GET /v1/datasets/{revision_id}/logs/{log_id}/values
```
Required:
```text
axis_id
```
Optional:
```text
from_m
to_m
after_sample
limit
```
Example:
```http
GET /v1/datasets/ca3660de-f678-5256-89f5-40d17832e4eb/logs/a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8/values?axis_id=b9ba28cd-0216-5314-83df-f6247b0f56b6&from_m=62.5&to_m=70&limit=1000
```
Typical row:
```json
{
   "sample_no": 15,
   "depth_m": 62.50175,
   "value_text": "Muscovite",
   "status": "available"
}
```
Missing source values may return:
```text
status = source_null
```
Do not replace missing values with fabricated mineral data.
---
## 9. Spectral Data
Spectra are requested through the exact-sample endpoint using a spectral `log_id`.
Known VSWIR example:
```text
log_id:
1936b71e-f3bc-56d8-ae0e-d284edf0a86b
channels:
531
```
Known TIR example:
```text
log_id:
8021acc8-2256-5810-b9dc-46ad2e7b7d64
channels:
341
```
A spectral result may contain:
```text
region_code
wavelength
spectra
status
```
If a log is `metadata_only`, no spectral values should be displayed.
---
## 10. Profile Data
```http
GET /v1/datasets/{revision_id}/profile-logs/{log_id}/values
```
Required:
```text
axis_id
```
Example profile log:
```text
92b30f38-bb34-57b9-af44-c0c5f5cb19e6
```
Current profile status:
```text
available_uncalibrated_profile
```
Each sample currently contains:
```text
64 values
```
The 64 positions do not currently have verified physical geometry.
Do not invent a physical x-axis.
---
## 11. Images
Exact sample responses may contain image mappings.
Useful fields include:
```text
image_asset_id
width_px
height_px
indicator
```
Retrieve the image bytes using:
```http
GET /v1/image-assets/{asset_id}/content
```
Example:
```http
GET /v1/image-assets/2a4b5519-b410-543f-a671-54ef1ae03f32/content
```
Known example image:
```text
399 x 25 px
```
The sample indicator returned by the backend is approximate.
---
## 12. Intervals
```http
GET /v1/datasets/{revision_id}/intervals
```
Required:
```text
axis_id
```
Optional:
```text
kind=tray
kind=section
offset
limit
```
Example:
```http
GET /v1/datasets/ca3660de-f678-5256-89f5-40d17832e4eb/intervals?axis_id=b9ba28cd-0216-5314-83df-f6247b0f56b6&kind=section&limit=50
```
---
## 13. Data Quality / QA Issues
```http
GET /v1/datasets/{revision_id}/issues
```
Returns ETL4 quality issues and source references.
The frontend should display these as source/data-quality information where useful.
---
## 14. Confidence
Task 2 has agreed that confidence is derived from the original Process Level.
Current mapping:
```text
Process Level 0 -> Low
Process Level 1 -> Medium
Process Level 2 -> High
```
The original `process_level` should remain available for traceability.
A separate value such as:
```text
confidence_level
```
should be used by the frontend.
Important:
The current confidence definition applies to the whole borehole/dataset, not individual depth samples.
The old Django prototype used a numeric per-depth confidence value. That should not be treated as the final confidence model.
---
## 15. Current Pilot Boreholes
```text
05KCD001
07THD002
07THD003
09ATD015
09ATD019
```
The API is database-driven and is not hard-coded to these five IDs.
Additional drillholes can be exposed when Task 1 adds them to the ETL4 database/release.
---
## 16. Error Handling
Typical API responses:
```text
200 = success
404 = dataset/sample/resource not found or not in active release
422 = invalid request parameters
503 = database/storage dependency problem
```
Frontend code should handle these instead of assuming every request succeeds.
---
## Suggested Frontend Flow
For a selected borehole:
```text
GET /v1/boreholes/{hole_id}/datasets
         ↓
get dataset_revision_id + axis_id
         ↓
GET /v1/datasets/{revision_id}/logs
         ↓
select mineral / spectral / image logs
         ↓
GET mineral depth values
         ↓
GET exact sample when the user selects a depth/sample
```
For the main map:
```text
GET /v1/boreholes
```
For nearby-hole selection:
```text
GET /v1/boreholes/nearby
```
---
## Notes
The current Django backend in the repository is an older prototype.
The FastAPI backend under:
```text
backend/
```
is intended to become the main backend.
Frontend changes are owned by Task 4.
The backend team will maintain the API contract and provide any endpoint changes required for integration.

