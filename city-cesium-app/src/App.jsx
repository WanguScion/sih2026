import { useEffect, useRef, useState } from "react";
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";

const API_DOMAIN = (import.meta.env.VITE_API_DOMAIN || "http://localhost:3000").replace(/\/$/, "");
const QUERY_PARCEL = import.meta.env.VITE_QUERY_PARCEL || "http://localhost:3000/query/:ulpin";

const SIZE = 500;
const ORIGIN = Cesium.Cartesian3.fromDegrees(0, 0, 0);
const LOCAL_TO_WORLD = Cesium.Transforms.eastNorthUpToFixedFrame(ORIGIN);

const BUILDING_COLOR = Cesium.Color.fromCssColorString("#858585");
const GROUND_COLOR = Cesium.Color.fromCssColorString("#666666");

function localToWorld(x, y, z = 0) {
  return Cesium.Matrix4.multiplyByPoint(
    LOCAL_TO_WORLD,
    new Cesium.Cartesian3(x, y, z),
    new Cesium.Cartesian3()
  );
}

function ringToWorld(ring, zOverride) {
  return ring.map((p) => {
    const x = Number(p[0]) || 0;
    const y = Number(p[1]) || 0;
    const z = zOverride ?? (Number(p[2]) || 0);
    return localToWorld(x, y, z);
  });
}

function getRings(feature) {
  const geometry = feature?.geometry;
  if (!geometry) return [];

  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates.flat(1);
  return [];
}

function getHeight(feature, coordinates) {
  const props = feature?.properties || {};
  const explicit = Number(
    props.height ?? props.Height ?? props.HEIGHT ?? props.building_height ?? props.buildingHeight
  );

  if (Number.isFinite(explicit) && explicit > 0) return Math.min(explicit, SIZE);

  const zValues = [];
  const walk = (value) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === "number") {
      if (value.length > 2) zValues.push(Number(value[2]) || 0);
      return;
    }
    value.forEach(walk);
  };
  walk(coordinates);

  if (zValues.length) {
    const min = Math.min(...zValues);
    const max = Math.max(...zValues);
    if (max > min) return Math.min(max - min, SIZE);
    if (max > 0) return Math.min(max, SIZE);
  }

  return 20;
}

function getBaseHeight(feature, coordinates) {
  const props = feature?.properties || {};
  const explicit = Number(props.baseHeight ?? props.base_height ?? props.minHeight ?? props.min_height);
  if (Number.isFinite(explicit)) return Math.max(0, Math.min(explicit, SIZE));

  const zValues = [];
  const walk = (value) => {
    if (!Array.isArray(value)) return;
    if (typeof value[0] === "number") {
      if (value.length > 2) zValues.push(Number(value[2]) || 0);
      return;
    }
    value.forEach(walk);
  };
  walk(coordinates);

  return zValues.length ? Math.max(0, Math.min(Math.min(...zValues), SIZE)) : 0;
}

function normalizeFeatures(data) {
  if (Array.isArray(data)) return data;
  if (data?.type === "FeatureCollection") return data.features || [];
  if (data?.type === "Feature") return [data];
  if (Array.isArray(data?.features)) return data.features;
  return [];
}

function getId(feature, index) {
  const p = feature?.properties || {};
  return String(
    p.ulpin ??
    p.ULPIN ??
    p.ulpin_id ??
    p.id ??
    feature?.id ??
    index
  );
}

function createGround(viewer) {
  const hierarchy = new Cesium.PolygonHierarchy([
    localToWorld(0, 0, 0),
    localToWorld(SIZE, 0, 0),
    localToWorld(SIZE, SIZE, 0),
    localToWorld(0, SIZE, 0),
  ]);

  const ground = viewer.entities.add({
    id: "city-ground",
    polygon: {
      hierarchy,
      height: 0,
      material: GROUND_COLOR,
      outline: true,
      outlineColor: Cesium.Color.fromCssColorString("#777777"),
    },
  });

  return ground;
}

function createBuildingPrimitive(viewer, feature, index) {
  const rings = getRings(feature);
  if (!rings.length) return null;

  const outer = rings[0];
  if (!outer || outer.length < 3) return null;

  const coordinates = feature.geometry.coordinates;
  const baseHeight = getBaseHeight(feature, coordinates);
  const height = getHeight(feature, coordinates);
  const topHeight = Math.min(baseHeight + height, SIZE);

  const positions = ringToWorld(outer, baseHeight);

  const holes = rings.slice(1).map(
    (ring) => new Cesium.PolygonHierarchy(ringToWorld(ring, baseHeight))
  );

  const geometry = new Cesium.PolygonGeometry({
    polygonHierarchy: new Cesium.PolygonHierarchy(positions, holes),
    height: baseHeight,
    extrudedHeight: topHeight,
    vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT,
  });

  const instance = new Cesium.GeometryInstance({
    geometry,
    id: {
      feature,
      index,
      ulpin: getId(feature, index),
    },
    attributes: {
      color: Cesium.ColorGeometryInstanceAttribute.fromColor(BUILDING_COLOR),
    },
  });

  const primitive = viewer.scene.primitives.add(
    new Cesium.Primitive({
      geometryInstances: instance,
      appearance: new Cesium.PerInstanceColorAppearance({
        flat: false,
        translucent: false,
        closed: true,
      }),
      asynchronous: false,
    })
  );

  primitive._cityFeature = feature;
  primitive._cityIndex = index;
  primitive._cityUlpIn = getId(feature, index);

  return primitive;
}

async function fetchCityData() {
  const response = await fetch(`${API_DOMAIN}/`);
  if (!response.ok) throw new Error(`City API returned ${response.status}`);
  return response.json();
}

async function fetchParcel(ulpin) {
  const url = QUERY_PARCEL.replace(":ulpin", encodeURIComponent(ulpin));
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Parcel API returned ${response.status}`);
  return response.json();
}

export default function App() {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const primitivesRef = useRef([]);

  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [parcel, setParcel] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;

    const viewer = new Cesium.Viewer(containerRef.current, {
      animation: false,
      timeline: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
      shouldAnimate: false,
    });

    viewerRef.current = viewer;
    viewer.scene.globe.show = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#202020");
    viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;

    createGround(viewer);

    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

    handler.setInputAction(async (movement) => {
      const picked = viewer.scene.pick(movement.position);
      if (!Cesium.defined(picked) || !picked.id?.ulpin && !picked.primitive?._cityUlpIn) return;

      const ulpin = String(picked.id?.ulpin ?? picked.primitive._cityUlpIn);
      const feature = picked.id?.feature ?? picked.primitive._cityFeature;

      setSelected({ ulpin, feature });
      setParcel(null);

      const primitive = picked.primitive;
      if (primitive?.boundingSphere) {
        viewer.camera.flyToBoundingSphere(primitive.boundingSphere, {
          duration: 0.6,
          offset: new Cesium.HeadingPitchRange(
            0,
            Cesium.Math.toRadians(-30),
            180
          ),
        });
      } else if (feature) {
        const rings = getRings(feature);
        const positions = ringToWorld(rings[0] || [], getBaseHeight(feature, feature.geometry.coordinates));
        if (positions.length) {
          const sphere = Cesium.BoundingSphere.fromPoints(positions);
          viewer.camera.flyToBoundingSphere(sphere, {
            duration: 0.6,
            offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-30), 180),
          });
        }
      }

      try {
        const data = await fetchParcel(ulpin);
        if (mounted) setParcel(data);
      } catch (err) {
        if (mounted) setParcel({ error: err.message });
      }
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);

    (async () => {
      try {
        const data = await fetchCityData();
        if (!mounted) return;

        const features = normalizeFeatures(data);
        primitivesRef.current = features
          .map((feature, index) => createBuildingPrimitive(viewer, feature, index))
          .filter(Boolean);

        viewer.camera.flyTo({
          destination: Cesium.Cartesian3.fromDegrees(0.00225, 0.00225, 850),
          orientation: {
            heading: Cesium.Math.toRadians(0),
            pitch: Cesium.Math.toRadians(-55),
            roll: 0,
          },
          duration: 0.8,
        });

        setLoading(false);
      } catch (err) {
        if (mounted) {
          setError(err.message);
          setLoading(false);
        }
      }
    })();

    return () => {
      mounted = false;
      handler.destroy();
      primitivesRef.current.forEach((p) => {
        if (!p.isDestroyed()) viewer.scene.primitives.remove(p);
      });
      viewer.destroy();
    };
  }, []);

  return (
    <div className="app">
      <div ref={containerRef} className="viewer" />

      <div className="hud">
        <div className="title">3D CITY</div>
        <div className="subtitle">500m × 500m × 500m local space</div>
        {loading && <div className="status">Loading city…</div>}
        {error && <div className="error">{error}</div>}
      </div>

      {selected && (
        <aside className="parcel-panel">
          <div className="panel-header">
            <span>PARCEL</span>
            <button onClick={() => { setSelected(null); setParcel(null); }}>×</button>
          </div>

          <div className="ulpin">{selected.ulpin}</div>

          {parcel ? (
            <pre>{JSON.stringify(parcel, null, 2)}</pre>
          ) : (
            <div className="loading-parcel">Fetching parcel details…</div>
          )}
        </aside>
      )}
    </div>
  );
}
