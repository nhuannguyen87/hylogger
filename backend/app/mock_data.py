# Temporary development data.
# This will be replaced by PostgreSQL/PostGIS data from the ETL team.

holes = [
    {
        "hole_id": "05GJD001",
        "project": "Lake Magenta",
        "latitude": -33.721898,
        "longitude": 118.963640,
        "scan_from_m": 150.0,
        "scan_to_m": 316.0,
        "instrument": "HyLogger 3-2",
    }
]


profiles = {
    "05GJD001": [
        {
            "from_depth_m": 219.9,
            "to_depth_m": 220.5,
            "mineral": "Kaolin",
            "uncertainty": "low",
        },
        {
            "from_depth_m": 220.5,
            "to_depth_m": 221.3,
            "mineral": "Chlorite",
            "uncertainty": "medium",
        },
        {
            "from_depth_m": 221.3,
            "to_depth_m": 222.0,
            "mineral": "White mica",
            "uncertainty": "high",
        },
    ]
}