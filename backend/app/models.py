from pydantic import BaseModel


class Hole(BaseModel):
    hole_id: str
    project: str
    latitude: float
    longitude: float
    scan_from_m: float
    scan_to_m: float
    instrument: str


class MineralProfile(BaseModel):
    from_depth_m: float
    to_depth_m: float
    mineral: str
    uncertainty: str


class GeoJSONGeometry(BaseModel):
    type: str
    coordinates: list[float]


class GeoJSONFeature(BaseModel):
    type: str
    geometry: GeoJSONGeometry
    properties: dict


class GeoJSONFeatureCollection(BaseModel):
    type: str
    features: list[GeoJSONFeature]