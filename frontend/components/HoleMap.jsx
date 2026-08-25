"use client";

// MapLibre, used directly rather than through a React wrapper. It is about 60
// lines and you can see exactly what it does, which matters more than brevity
// while you're learning.
//
// The pattern: create the map once, then push new data into a GeoJSON source
// whenever `holes` changes. Never recreate the map on every render.

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MAP_START, MAP_STYLE } from "@/config";

const SOURCE = "holes";

export default function HoleMap({ holes = [], selectedId, onSelect }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const readyRef = useRef(false);

  // 1. create the map, once
  useEffect(() => {
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE,
      center: [MAP_START.longitude, MAP_START.latitude],
      zoom: MAP_START.zoom,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }));

    map.on("load", () => {
      map.addSource(SOURCE, { type: "geojson", data: emptyCollection() });

      map.addLayer({
        id: "holes-circles",
        type: "circle",
        source: SOURCE,
        paint: {
          // grow the dots as you zoom in
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 3, 8, 7, 12, 11],
          "circle-color": [
            "case",
            ["boolean", ["feature-state", "selected"], false], "#4fd1c5",
            "#8c9aa5",
          ],
          "circle-stroke-width": [
            "case",
            ["boolean", ["feature-state", "selected"], false], 2, 0.5,
          ],
          "circle-stroke-color": "#0e1418",
        },
      });

      map.on("click", "holes-circles", (event) => {
        const feature = event.features?.[0];
        if (feature) onSelect?.(feature.properties.hole_id);
      });
      map.on("mouseenter", "holes-circles", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "holes-circles", () => {
        map.getCanvas().style.cursor = "";
      });

      readyRef.current = true;
      mapRef.current = map;
      pushData(map, holes);
    });

    mapRef.current = map;
    return () => map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 2. push new holes into the map whenever the list changes
  useEffect(() => {
    if (mapRef.current && readyRef.current) pushData(mapRef.current, holes);
  }, [holes]);

  // 3. highlight the selected hole
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    holes.forEach((hole) => {
      map.setFeatureState(
        { source: SOURCE, id: hole.hole_id },
        { selected: hole.hole_id === selectedId }
      );
    });

    const selected = holes.find((hole) => hole.hole_id === selectedId);
    if (selected) {
      map.easeTo({ center: [selected.longitude, selected.latitude], duration: 600 });
    }
  }, [selectedId, holes]);

  return <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />;
}

function pushData(map, holes) {
  const source = map.getSource(SOURCE);
  if (!source) return;
  source.setData({
    type: "FeatureCollection",
    features: holes.map((hole) => ({
      type: "Feature",
      id: hole.hole_id, // needed for setFeatureState
      properties: { hole_id: hole.hole_id, hole_name: hole.hole_name },
      geometry: { type: "Point", coordinates: [hole.longitude, hole.latitude] },
    })),
  });
}

const emptyCollection = () => ({ type: "FeatureCollection", features: [] });
