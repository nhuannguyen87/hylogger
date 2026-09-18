from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check():
    response = client.get("/api/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
    assert data["database"] == "etl4_core"
    assert data["database_role"] == "etl4_reader"
    assert (
        data["release_id"]
        == "fd02659c-dc3d-52ea-a8eb-c032b91e7624"
    )
    assert data["asset_root_available"] is True


def test_get_holes():
    response = client.get("/api/holes")

    assert response.status_code == 200
    assert len(response.json()) >= 1
    assert response.json()[0]["hole_id"] == "05KCD001"


def test_get_hole():
    response = client.get("/api/holes/05KCD001")

    assert response.status_code == 200
    assert response.json()["hole_id"] == "05KCD001"


def test_geojson():
    response = client.get("/api/holes/geojson")

    assert response.status_code == 200

    data = response.json()

    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) >= 1


def test_get_real_sample():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples/15",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "log_ids": "a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["md_m"] == 62.50175
    assert data["results"][0]["value"]["value_text"] == "Muscovite"
    assert data["results"][0]["status"] == "available"
    assert data["image_status"] == "available"
    assert data["images"][0]["width_px"] == 399
    assert data["images"][0]["height_px"] == 25


def test_get_real_image_asset():
    response = client.get(
        "/v1/image-assets/"
        "2a4b5519-b410-543f-a671-54ef1ae03f32/"
        "content"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert len(response.content) == 22719
    assert (
        response.headers["etag"]
        == '"bdaa8cf11459a648506b6821afa1ae6d50be28e32b63d6762b48bba8fe991ed1"'
    )


def test_get_borehole_datasets():
    response = client.get(
        "/v1/boreholes/07THD002/datasets"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["hole_id"] == "07THD002"
    assert len(data["items"]) >= 1

    dataset = data["items"][0]

    assert (
        dataset["dataset_revision_id"]
        == "ca3660de-f678-5256-89f5-40d17832e4eb"
    )
    assert (
        dataset["axis_id"]
        == "b9ba28cd-0216-5314-83df-f6247b0f56b6"
    )
    assert dataset["sample_count"] == 116424


def test_get_dataset_logs():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "logs"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 127

    log_ids = {
        item["log_id"]
        for item in data["items"]
    }

    assert (
        "a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8"
        in log_ids
    )


def test_get_dataset_samples():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "from_m": 62.5,
            "to_m": 63.0,
            "limit": 5,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 5
    assert data["next_after_sample"] == 4

    assert data["items"][0]["sample_no"] == 0
    assert data["items"][-1]["sample_no"] == 4

    assert data["items"][0]["md_m"] == 62.50175
    assert data["items"][1]["md_m"] == 62.50175


def test_get_vswir_spectrum():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples/15",
        params=[
            (
                "axis_id",
                "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            ),
            (
                "log_ids",
                "1936b71e-f3bc-56d8-ae0e-d284edf0a86b",
            ),
        ],
    )

    assert response.status_code == 200

    result = response.json()["results"][0]

    assert result["status"] == "available"
    assert result["region_code"] == "VSWIR"
    assert len(result["wavelength"]) == 531
    assert len(result["spectra"]) == 531
    assert result["wavelength"][:5] == [
        380.0,
        384.0,
        388.0,
        392.0,
        396.0,
    ]


def test_get_tir_spectrum():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples/15",
        params=[
            (
                "axis_id",
                "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            ),
            (
                "log_ids",
                "8021acc8-2256-5810-b9dc-46ad2e7b7d64",
            ),
        ],
    )

    assert response.status_code == 200

    result = response.json()["results"][0]

    assert result["status"] == "available"
    assert result["region_code"] == "TIR"
    assert len(result["wavelength"]) == 341
    assert len(result["spectra"]) == 341
    assert result["wavelength"][:5] == [
        6000.0,
        6025.0,
        6050.0,
        6075.0,
        6100.0,
    ]


def test_get_dataset_intervals():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "intervals",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "kind": "section",
            "limit": 5,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 5
    assert data["next_offset"] == 5
    assert data["items"][0]["interval_kind"] == "section"
    assert data["items"][0]["ordinal"] == 0
    assert data["items"][0]["sample_no_from"] == 0
    assert data["items"][0]["sample_no_to"] == 251


def test_get_dataset_issues():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "issues"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 22
    assert len(data["source_references"]) == 3
    assert (
        data["items"][0]["code"]
        == "SPECTRAL_MATRIX_ORDER_UNCONFIRMED"
    )


def test_get_nearby_boreholes():
    response = client.get(
        "/v1/boreholes/nearby",
        params={
            "latitude": -27.13891542,
            "longitude": 118.51447554,
            "radius_km": 1,
            "limit": 5,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 2
    assert data["items"][0]["hole_id"] == "09ATD015"
    assert data["items"][1]["hole_id"] == "09ATD019"
    assert data["items"][0]["distance_km"] == 0.0
    assert data["items"][1]["distance_km"] < 0.1


def test_get_v1_boreholes_with_bbox():
    response = client.get(
        "/v1/boreholes",
        params={
            "bbox": "118.4,-27.3,118.7,-27.0",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["items"]) == 2

    hole_ids = [
        item["hole_id"]
        for item in data["items"]
    ]

    assert hole_ids == [
        "09ATD015",
        "09ATD019",
    ]

    assert data["items"][0]["geometry"]["type"] == "Point"


def test_get_scalar_log_values():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "logs/"
        "a4a5c7d3-0fe8-53be-97a2-02a5c7771ae8/"
        "values",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "from_m": 62.5,
            "to_m": 62.6,
            "limit": 20,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "available"
    assert len(data["items"]) == 20
    assert data["next_after_sample"] == 19

    assert data["items"][0]["status"] == "source_null"
    assert data["items"][15]["sample_no"] == 15
    assert data["items"][15]["value_text"] == "Muscovite"
    assert data["items"][15]["status"] == "available"


def test_get_profile_log_values():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "profile-logs/"
        "92b30f38-bb34-57b9-af44-c0c5f5cb19e6/"
        "values",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "from_m": 62.5,
            "to_m": 62.55,
            "limit": 5,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "available_uncalibrated_profile"
    assert data["physical_geometry_status"] == "uncalibrated"
    assert len(data["items"]) == 5
    assert data["next_after_sample"] == 4
    assert data["items"][0]["value_count"] == 64
    assert (
        data["items"][0]["status"]
        == "available_uncalibrated_profile"
    )

def test_invalid_bbox_returns_422():
    response = client.get(
        "/v1/boreholes",
        params={
            "bbox": "200,-27.3,118.7,-27.0",
        },
    )

    assert response.status_code == 422

def test_invalid_depth_range_returns_422():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            "from_m": 100.0,
            "to_m": 50.0,
        },
    )

    assert response.status_code == 422

def test_unknown_revision_returns_404():
    response = client.get(
        "/v1/datasets/"
        "00000000-0000-0000-0000-000000000000/"
        "samples/0",
        params={
            "axis_id": "b9ba28cd-0216-5314-83df-f6247b0f56b6",
        },
    )

    assert response.status_code == 404

def test_metadata_only_spectral_log_stays_metadata_only():
    response = client.get(
        "/v1/datasets/"
        "ca3660de-f678-5256-89f5-40d17832e4eb/"
        "samples/15",
        params=[
            (
                "axis_id",
                "b9ba28cd-0216-5314-83df-f6247b0f56b6",
            ),
            (
                "log_ids",
                "cba75bf0-3dc5-5aae-982b-d268aa0bba47",
            ),
        ],
    )

    assert response.status_code == 200

    result = response.json()["results"][0]

    assert result["log_id"] == (
        "cba75bf0-3dc5-5aae-982b-d268aa0bba47"
    )
    assert result["log_kind"] == "spectral"
    assert result["status"] == "metadata_only"
    assert result["value"] is None

def test_repeated_depth_samples_remain_distinct():
    base = (
        "/v1/datasets/"
        "1a5e0eac-2004-5efa-a434-9335b297c7c3/"
        "samples/"
    )

    params = {
        "axis_id": "6b1e2af2-3e03-522b-8d23-11615999ad18",
    }

    sample_129 = client.get(
        base + "129",
        params=params,
    )

    sample_130 = client.get(
        base + "130",
        params=params,
    )

    assert sample_129.status_code == 200
    assert sample_130.status_code == 200

    first = sample_129.json()
    second = sample_130.json()

    assert first["sample_no"] == 129
    assert second["sample_no"] == 130

    assert first["md_m"] == 60.37269
    assert second["md_m"] == 60.37269

    assert (
        first["images"][0]["image_asset_id"]
        != second["images"][0]["image_asset_id"]
    )

    assert (
        first["images"][0]["image_region_id"]
        != second["images"][0]["image_region_id"]
    )

def test_09atd015_does_not_expose_tir_spectral_logs():
    response = client.get(
        "/v1/datasets/"
        "47cf05e4-91f5-521d-bda8-cbffa2e9ae71/"
        "logs"
    )

    assert response.status_code == 200

    spectral_logs = [
        item
        for item in response.json()["items"]
        if item["log_kind"] == "spectral"
    ]

    assert len(spectral_logs) >= 1
    assert all(
        item["spectral_region"] != "TIR"
        for item in spectral_logs
    )
