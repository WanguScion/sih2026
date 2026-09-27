import React, { useEffect, useRef, useState, useCallback } from "react";
import * as Cesium from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import {
  MOCK_BUILDINGS_FEATURE_COLLECTION,
  MOCK_CONFLICTS_DATA,
  getMockBuildingOrFloor
} from "./cadastralMockData.js";

const API_DOMAIN = (import.meta.env.VITE_API_DOMAIN || "http://localhost:3000").replace(/\/+$/, "");
const QUERY_PARCEL = import.meta.env.VITE_QUERY_PARCEL || `${API_DOMAIN}/query/:ulpin/`;

const DEFAULT_LON = parseFloat(import.meta.env.VITE_DEFAULT_VIEW_LON) || 76.3548;
const DEFAULT_LAT = parseFloat(import.meta.env.VITE_DEFAULT_VIEW_LAT) || 10.0125;
const DEFAULT_HEIGHT = parseFloat(import.meta.env.VITE_DEFAULT_VIEW_HEIGHT) || 350;

export default function App() {
  const containerRef = useRef(null);
  const viewerRef = useRef(null);
  const handlerRef = useRef(null);

  // References to active 3D primitives in Cesium scene
  const buildingPrimitivesRef = useRef(new Map()); // ulpin -> primitive
  const floorPrimitivesRef = useRef([]); // array of active floor primitives
  const allBuildingsDataRef = useRef([]); // array of building features

  // State
  const [loading, setLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState(null);
  const [dataSource, setDataSource] = useState("connecting"); // "live" | "fallback"
  const [buildings, setBuildings] = useState([]);
  const [conflicts, setConflicts] = useState([]);
  const [showConflictPanel, setShowConflictPanel] = useState(false);

  // Selection state
  const [selectedBuilding, setSelectedBuilding] = useState(null);
  const [selectedFloor, setSelectedFloor] = useState(null);
  const [buildingDetailsLoading, setBuildingDetailsLoading] = useState(false);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchError, setSearchError] = useState(null);

  // Remove existing floor primitives from the scene
  const clearFloorPrimitives = useCallback(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    floorPrimitivesRef.current.forEach((prim) => {
      try {
        if (prim && !prim.isDestroyed()) {
          viewer.scene.primitives.remove(prim);
        }
      } catch {
        // already removed
      }
    });
    floorPrimitivesRef.current = [];
  }, []);

  // Restore all building envelopes to default opacity
  const resetBuildingEnvelopes = useCallback(() => {
    buildingPrimitivesRef.current.forEach((prim) => {
      if (prim && !prim.isDestroyed() && prim._defaultColor) {
        try {
          const attributes = prim.getGeometryInstanceAttributes(prim._instanceId);
          if (attributes) {
            attributes.color = Cesium.ColorGeometryInstanceAttribute.toValue(prim._defaultColor);
          }
        } catch {
          // ignore attribute update error
        }
      }
    });
  }, []);

  // Step 3: Render per-floor extruded primitives for the selected building
  const renderBuildingFloors = useCallback((buildingData) => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    // Clear any previous floors
    clearFloorPrimitives();

    const floors = buildingData.floors || [];
    if (floors.length === 0) return;

    const ringCoords = buildingData.geometry?.coordinates?.[0];
    if (!ringCoords || ringCoords.length < 3) return;

    // Make parent building envelope translucent so internal vertical floors are visible
    const parentPrim = buildingPrimitivesRef.current.get(buildingData.properties?.ulpin);
    if (parentPrim && !parentPrim.isDestroyed()) {
      try {
        const attributes = parentPrim.getGeometryInstanceAttributes(parentPrim._instanceId);
        if (attributes) {
          attributes.color = Cesium.ColorGeometryInstanceAttribute.toValue(
            Cesium.Color.fromCssColorString("#94a3b8").withAlpha(0.12)
          );
        }
      } catch {
        // ignore
      }
    }

    // Sort floors by floor_index ascending (basements first, then above-ground)
    const sortedFloors = [...floors].sort((a, b) => {
      const idxA = a.properties?.floor_index ?? 0;
      const idxB = b.properties?.floor_index ?? 0;
      return idxA - idxB;
    });

    const newFloorPrimitives = [];

    sortedFloors.forEach((floor) => {
      const p = floor.properties || {};
      const zMin = Number(p.z_min) || 0;
      const zMax = Number(p.z_max) || zMin + 3.2;
      const colorHex = p.color || "#3E8ED0";
      const floorUlpin = p.ulpin;

      const positions = ringCoords.map(([lon, lat]) =>
        Cesium.Cartesian3.fromDegrees(Number(lon), Number(lat), zMin)
      );

      const polygonHierarchy = new Cesium.PolygonHierarchy(positions);

      const floorGeometry = new Cesium.PolygonGeometry({
        polygonHierarchy,
        height: zMin,
        extrudedHeight: zMax,
        vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT,
      });

      const floorColor = Cesium.Color.fromCssColorString(colorHex);
      const instanceId = {
        type: "floor",
        ulpin: floorUlpin,
        floor_index: p.floor_index,
        building_ulpin: p.building_ulpin || buildingData.properties?.ulpin,
        floor_data: p,
      };

      const instance = new Cesium.GeometryInstance({
        geometry: floorGeometry,
        id: instanceId,
        attributes: {
          color: Cesium.ColorGeometryInstanceAttribute.fromColor(floorColor),
        },
      });

      const floorPrimitive = new Cesium.Primitive({
        geometryInstances: instance,
        appearance: new Cesium.PerInstanceColorAppearance({
          closed: true,
          translucent: false,
          flat: true,
        }),
        asynchronous: false,
      });

      floorPrimitive._ulpinData = instanceId;
      viewer.scene.primitives.add(floorPrimitive);
      newFloorPrimitives.push(floorPrimitive);
    });

    floorPrimitivesRef.current = newFloorPrimitives;
  }, [clearFloorPrimitives]);

  // Select a building by its ULPIN and fly camera to it
  const selectBuildingByUlpin = useCallback(async (ulpin, focusFloorUlpin = null) => {
    const viewer = viewerRef.current;
    setSearchError(null);
    setBuildingDetailsLoading(true);

    try {
      const cleanUlpin = ulpin.trim();
      let buildingData = null;
      let targetFloorData = null;

      // 1. Try querying the live backend API
      try {
        const res = await fetch(`${API_DOMAIN}/query/${encodeURIComponent(cleanUlpin)}/`, {
          signal: AbortSignal.timeout(2500),
        });
        if (res.ok) {
          const data = await res.json();
          if (data.properties?.building_ulpin) {
            // Query result was a single floor
            targetFloorData = data;
            const parentRes = await fetch(
              `${API_DOMAIN}/query/${encodeURIComponent(data.properties.building_ulpin)}/`,
              { signal: AbortSignal.timeout(2500) }
            );
            if (parentRes.ok) {
              buildingData = await parentRes.json();
            }
          } else {
            buildingData = data;
          }
        }
      } catch (err) {
        console.warn("[Backend] Query failed, checking built-in dummy data:", err);
      }

      // 2. If live query failed or returned nothing, fall back to built-in dummy dataset
      if (!buildingData) {
        const mockItem = getMockBuildingOrFloor(cleanUlpin);
        if (mockItem) {
          if (mockItem.properties?.building_ulpin) {
            targetFloorData = mockItem;
            const parent = getMockBuildingOrFloor(mockItem.properties.building_ulpin);
            if (parent) buildingData = parent;
          } else {
            buildingData = mockItem;
          }
        }
      }

      if (!buildingData) {
        throw new Error(`ULPIN '${cleanUlpin}' not found`);
      }

      if (focusFloorUlpin && !targetFloorData) {
        targetFloorData = (buildingData.floors || []).find(
          (f) => f.properties?.ulpin === focusFloorUlpin
        );
      }

      setSelectedBuilding(buildingData);
      setSelectedFloor(targetFloorData);

      // Render vertical floors for this building
      renderBuildingFloors(buildingData);

      // Fly camera to building
      if (viewer && !viewer.isDestroyed()) {
        const coords = buildingData.geometry?.coordinates?.[0];
        if (coords && coords.length > 0) {
          const cartestians = coords.map(([lon, lat]) =>
            Cesium.Cartesian3.fromDegrees(Number(lon), Number(lat), Number(buildingData.properties?.building_height || 15) / 2)
          );
          const sphere = Cesium.BoundingSphere.fromPoints(cartestians);
          viewer.camera.flyToBoundingSphere(sphere, {
            duration: 1.0,
            offset: new Cesium.HeadingPitchRange(
              viewer.camera.heading,
              Cesium.Math.toRadians(-28),
              Math.max(sphere.radius * 3.2, 75)
            ),
          });
        }
      }
    } catch (err) {
      setSearchError(err.message);
    } finally {
      setBuildingDetailsLoading(false);
    }
  }, [renderBuildingFloors]);

  // Step 5: Close selection and reset 3D scene
  const closeSelection = useCallback(() => {
    setSelectedBuilding(null);
    setSelectedFloor(null);
    clearFloorPrimitives();
    resetBuildingEnvelopes();
  }, [clearFloorPrimitives, resetBuildingEnvelopes]);

  const closeSelectionRef = useRef(closeSelection);
  closeSelectionRef.current = closeSelection;

  const selectBuildingByUlpinRef = useRef(selectBuildingByUlpin);
  selectBuildingByUlpinRef.current = selectBuildingByUlpin;

  // Handle ULPIN Search Form Submission
  const handleSearchSubmit = (e) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    selectBuildingByUlpin(searchQuery.trim());
  };

  // Fly back to overview
  const flyToOverview = () => {
    const viewer = viewerRef.current;
    if (!viewer || viewer.isDestroyed()) return;

    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(DEFAULT_LON, DEFAULT_LAT, DEFAULT_HEIGHT),
      orientation: {
        heading: Cesium.Math.toRadians(35),
        pitch: Cesium.Math.toRadians(-38),
        roll: 0.0,
      },
      duration: 1.2,
    });
  };

  // Main Scene Setup Effect
  useEffect(() => {
    let isMounted = true;
    let viewer = null;
    let handler = null;

    async function initCesium() {
      if (!containerRef.current) return;

      // Cesium Ion token if available
      const ionToken = import.meta.env.VITE_CESIUM_ION_TOKEN;
      if (ionToken && ionToken.trim()) {
        Cesium.Ion.defaultAccessToken = ionToken.trim();
      }

      // Step 1: Cesium Viewer setup with real world terrain & visible globe
      let terrainProvider = new Cesium.EllipsoidTerrainProvider();
      try {
        if (ionToken && ionToken.trim()) {
          terrainProvider = await Cesium.createWorldTerrainAsync();
        }
      } catch (err) {
        console.warn("[Cesium] World terrain unavailable, falling back to ellipsoid:", err);
      }

      viewer = new Cesium.Viewer(containerRef.current, {
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
        terrainProvider,
      });

      viewerRef.current = viewer;

      // Keep globe visible
      viewer.scene.globe.show = true;
      viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;

      // Set initial camera view
      viewer.camera.setView({
        destination: Cesium.Cartesian3.fromDegrees(DEFAULT_LON, DEFAULT_LAT, DEFAULT_HEIGHT),
        orientation: {
          heading: Cesium.Math.toRadians(35),
          pitch: Cesium.Math.toRadians(-38),
          roll: 0.0,
        },
      });

      // Fetch conflict records
      let loadedConflicts = MOCK_CONFLICTS_DATA;
      try {
        const confRes = await fetch(`${API_DOMAIN}/conflicts/`, {
          signal: AbortSignal.timeout(2500),
        });
        if (confRes.ok) {
          loadedConflicts = await confRes.json();
        }
      } catch (err) {
        console.warn("[Backend] Using built-in dummy conflicts dataset:", err);
      }
      if (isMounted) setConflicts(loadedConflicts);

      // Step 2: Fetch buildings from GET /query/
      let fc = null;
      let isLive = false;
      try {
        const res = await fetch(`${API_DOMAIN}/query/`, {
          signal: AbortSignal.timeout(2500),
        });
        if (res.ok) {
          fc = await res.json();
          isLive = true;
        } else {
          console.warn(`[Backend] API returned status ${res.status}, using built-in dummy parcels`);
        }
      } catch (err) {
        console.warn("[Backend] Backend offline or unreachable. Using built-in dummy parcels:", err);
      }

      // If backend was offline, fall back to full dummy dataset
      if (!fc || !fc.features || fc.features.length === 0) {
        fc = MOCK_BUILDINGS_FEATURE_COLLECTION;
      }

      if (!isMounted) return;
      setDataSource(isLive ? "live" : "fallback");

      const features = fc.features || [];
      allBuildingsDataRef.current = features;
      setBuildings(features);

      const allPositions = [];

      // Step 2: Render each building as its own primitive
      features.forEach((feature, idx) => {
        const p = feature.properties || {};
        const ulpin = p.ulpin || `bldg-${idx}`;
        const height = Number(p.building_height) || 12.0;
        const hasConflict = Boolean(p.has_conflict);

        const ringCoords = feature.geometry?.coordinates?.[0];
        if (!Array.isArray(ringCoords) || ringCoords.length < 3) return;

        const positions = ringCoords.map(([lon, lat]) => {
          allPositions.push([lon, lat]);
          return Cesium.Cartesian3.fromDegrees(Number(lon), Number(lat), 0);
        });

        const polygonHierarchy = new Cesium.PolygonHierarchy(positions);

        const polygonGeometry = new Cesium.PolygonGeometry({
          polygonHierarchy,
          height: 0,
          extrudedHeight: height,
          vertexFormat: Cesium.PerInstanceColorAppearance.VERTEX_FORMAT,
        });

        // Visually distinguish conflicted properties with vibrant red highlight
        const buildingColor = hasConflict
          ? Cesium.Color.fromCssColorString("#EF4444").withAlpha(0.85) // Red warning highlight
          : Cesium.Color.fromCssColorString("#38BDF8").withAlpha(0.70); // Cyan/blue normal property

        const instanceId = `bldg-${ulpin}`;
        const instance = new Cesium.GeometryInstance({
          id: instanceId,
          geometry: polygonGeometry,
          attributes: {
            color: Cesium.ColorGeometryInstanceAttribute.fromColor(buildingColor),
          },
        });

        const primitive = new Cesium.Primitive({
          geometryInstances: instance,
          appearance: new Cesium.PerInstanceColorAppearance({
            closed: true,
            translucent: false,
            flat: true,
          }),
          asynchronous: false,
        });

        // Stash metadata for interaction & styling
        primitive._instanceId = instanceId;
        primitive._defaultColor = buildingColor;
        primitive._ulpinData = {
          type: "building",
          ulpin,
          has_conflict: hasConflict,
          feature,
        };

        viewer.scene.primitives.add(primitive);
        buildingPrimitivesRef.current.set(ulpin, primitive);
      });

      // Step 4: Initial camera fly-in to survey extent
      if (allPositions.length > 0) {
        const cartestians = allPositions.map(([lon, lat]) =>
          Cesium.Cartesian3.fromDegrees(Number(lon), Number(lat), 10)
        );
        const boundingSphere = Cesium.BoundingSphere.fromPoints(cartestians);
        viewer.camera.flyToBoundingSphere(boundingSphere, {
          duration: 1.0,
          offset: new Cesium.HeadingPitchRange(
            Cesium.Math.toRadians(35),
            Cesium.Math.toRadians(-35),
            Math.max(boundingSphere.radius * 3.5, 320)
          ),
        });
      }

      setLoading(false);

      // Step 5: Click interaction via ScreenSpaceEventHandler
      handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
      handlerRef.current = handler;

      handler.setInputAction(async (movement) => {
        const picked = viewer.scene.pick(movement.position);
        if (!Cesium.defined(picked)) {
          // Clicking empty space clears selection
          closeSelectionRef.current?.();
          return;
        }

        // Check if picked is a floor primitive or building primitive
        const pickData =
          (picked.id && typeof picked.id === "object" && picked.id.type)
            ? picked.id
            : picked.primitive?._ulpinData;

        if (!pickData) {
          closeSelectionRef.current?.();
          return;
        }

        if (pickData.type === "floor") {
          // Second-level drill-down: floor picked
          const floorUlpin = pickData.ulpin;
          let floorData = pickData.floor_data;
          try {
            const fRes = await fetch(`${API_DOMAIN}/query/${encodeURIComponent(floorUlpin)}/`, {
              signal: AbortSignal.timeout(2000),
            });
            if (fRes.ok) {
              const fJson = await fRes.json();
              floorData = fJson.properties || fJson;
            }
          } catch {
            // fallback to pickData.floor_data or mock
          }
          if (!floorData) {
            const mockFloor = getMockBuildingOrFloor(floorUlpin);
            if (mockFloor) floorData = mockFloor.properties || mockFloor;
          }
          setSelectedFloor(floorData);
        } else if (pickData.type === "building" || pickData.ulpin) {
          // First-level pick: building picked
          selectBuildingByUlpinRef.current?.(pickData.ulpin);
        }
      }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    }

    initCesium();

    // Step 9: Cleanup on unmount
    return () => {
      isMounted = false;
      if (handler && !handler.isDestroyed()) {
        handler.destroy();
      }
      if (viewer && !viewer.isDestroyed()) {
        buildingPrimitivesRef.current.forEach((prim) => {
          try {
            if (prim && !prim.isDestroyed()) viewer.scene.primitives.remove(prim);
          } catch {
            // ignore
          }
        });
        buildingPrimitivesRef.current.clear();

        floorPrimitivesRef.current.forEach((prim) => {
          try {
            if (prim && !prim.isDestroyed()) viewer.scene.primitives.remove(prim);
          } catch {
            // ignore
          }
        });
        floorPrimitivesRef.current = [];

        viewer.destroy();
      }
    };
  }, []);

  return (
    <div className="app-container">
      {/* Step 8: HUD (Header, Search Bar, Conflict Controls, Badges) */}
      <header className="hud">
        <div className="hud-header">
          <div className="hud-badge-row">
            <span className="hud-badge">PS 26011</span>
            <span className="hud-badge crs">WGS84 EPSG:4326</span>
            <span className={`hud-badge ${dataSource === "live" ? "live" : "offline"}`}>
              {dataSource === "live" ? "● LIVE BACKEND" : "● OFFLINE DEMO DATA"}
            </span>
          </div>
          <h1 className="hud-title">3D ULPIN PROPERTY VIEWER</h1>
          <p className="hud-subtitle">Kochi Infopark & Smart City Cadastral Zone</p>
        </div>

        {/* Step 6: ULPIN Search Box */}
        <form className="search-form" onSubmit={handleSearchSubmit}>
          <div className="search-input-wrapper">
            <span className="search-icon">🔍</span>
            <input
              type="text"
              className="search-input"
              placeholder="Search by ULPIN (e.g. KL-14-0231-9F72)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="search-clear-btn"
                onClick={() => setSearchQuery("")}
              >
                ✕
              </button>
            )}
          </div>
          <button type="submit" className="search-btn">
            Locate
          </button>
        </form>

        {searchError && (
          <div className="search-error">
            <span>⚠ {searchError}</span>
          </div>
        )}

        {/* HUD Toolbar & Stats */}
        <div className="hud-toolbar">
          <button
            className={`conflict-toggle-btn ${showConflictPanel ? "active" : ""}`}
            onClick={() => setShowConflictPanel(!showConflictPanel)}
            title="Inspect Cadastral Conflicts"
          >
            <span className="conflict-pulse"></span>
            Conflicts ({conflicts.length})
          </button>

          <button className="reset-view-btn" onClick={flyToOverview} title="Reset camera to overview">
            ↺ Overview
          </button>
        </div>

        {/* Status indicator */}
        <div className="hud-status">
          {loading && (
            <span className="status-loading">
              <span className="spinner"></span> Loading 3D parcels…
            </span>
          )}
          {errorMessage && <span className="status-error">⚠ {errorMessage}</span>}
          {!loading && !errorMessage && (
            <span className="status-ready">
              <span
                className="ready-dot"
                style={{ backgroundColor: dataSource === "live" ? "#10b981" : "#f59e0b" }}
              ></span>
              {buildings.length} Properties Active ({dataSource === "live" ? "API Connected" : "Self-Contained Mode"})
            </span>
          )}
        </div>
      </header>

      {/* Step 7: Conflict Review Panel */}
      {showConflictPanel && (
        <aside className="conflict-panel">
          <div className="panel-top-row">
            <div>
              <span className="panel-kicker">CADASTRAL AUDIT</span>
              <h2 className="panel-heading">Detected Conflicts ({conflicts.length})</h2>
            </div>
            <button
              className="close-icon-btn"
              onClick={() => setShowConflictPanel(false)}
              aria-label="Close conflicts panel"
            >
              ×
            </button>
          </div>
          <p className="conflict-desc">
            Autonomous overlap & buffer violations detected by the SegFormer GIS pipeline. Click any conflict to navigate directly:
          </p>

          <div className="conflict-list">
            {conflicts.map((c) => (
              <div
                key={c.conflict_id}
                className="conflict-card"
                onClick={() => {
                  const targetUlpin = c.ulpins?.[0];
                  if (targetUlpin) selectBuildingByUlpin(targetUlpin);
                }}
              >
                <div className="conflict-card-header">
                  <span className={`severity-tag ${c.severity}`}>
                    {c.severity.toUpperCase()}
                  </span>
                  <span className="conflict-type-tag">{c.conflict_type}</span>
                </div>
                <h3 className="conflict-card-title">{c.conflict_id}</h3>
                <p className="conflict-card-body">{c.description}</p>
                <div className="conflict-ulpins">
                  <span>Involved ULPINs:</span>
                  <div className="ulpin-pill-row">
                    {c.ulpins?.map((u) => (
                      <span key={u} className="ulpin-pill">
                        {u}
                      </span>
                    ))}
                  </div>
                </div>
                {c.remediation_action && (
                  <p className="conflict-action">
                    <strong>Action:</strong> {c.remediation_action}
                  </p>
                )}
              </div>
            ))}
          </div>
        </aside>
      )}

      {/* Cesium Canvas Container */}
      <div ref={containerRef} className="cesium-container" />

      {/* Step 8: Selected Property & Per-Floor Inspector Side Panel */}
      {selectedBuilding && (
        <aside className="property-panel">
          <div className="panel-top-row">
            <div>
              <span className="panel-kicker">PROPERTY DOSSIER</span>
              <h2 className="property-ulpin">{selectedBuilding.properties?.ulpin}</h2>
            </div>
            <button
              className="close-icon-btn"
              onClick={closeSelection}
              aria-label="Close property panel"
            >
              ×
            </button>
          </div>

          {buildingDetailsLoading ? (
            <div className="loading-state">
              <span className="spinner large"></span>
              <p>Fetching vertical property mapping…</p>
            </div>
          ) : (
            <div className="panel-scroll-content">
              {/* Conflict Alert Banner if applicable */}
              {selectedBuilding.properties?.has_conflict && (
                <div className="property-conflict-banner">
                  <span className="banner-icon">⚠</span>
                  <div className="banner-text">
                    <strong>Boundary Conflict Flagged</strong>
                    <p>
                      {selectedBuilding.properties?.conflict_info?.description ||
                        "This property has overlapping parcel boundaries with adjacent parcels."}
                    </p>
                  </div>
                </div>
              )}

              {/* Property Snapshot */}
              <div className="property-overview-card">
                <div className="overview-row">
                  <span className="label">Building Name:</span>
                  <span className="value bold">{selectedBuilding.properties?.name}</span>
                </div>
                <div className="overview-row">
                  <span className="label">Structure Class:</span>
                  <span className="value capitalize">{selectedBuilding.properties?.class_name?.replace("_", " ")}</span>
                </div>
                <div className="overview-row">
                  <span className="label">Total Height:</span>
                  <span className="value">{selectedBuilding.properties?.building_height} m</span>
                </div>
                <div className="overview-row">
                  <span className="label">Total Floors:</span>
                  <span className="value">{selectedBuilding.properties?.floor_count} storeys</span>
                </div>
                <div className="overview-row">
                  <span className="label">Survey Number:</span>
                  <span className="value">{selectedBuilding.properties?.cadastral_survey?.survey_no || "Sy. 142/2"}</span>
                </div>
                <div className="overview-row">
                  <span className="label">Jurisdiction:</span>
                  <span className="value">
                    {selectedBuilding.properties?.cadastral_survey?.village}, {selectedBuilding.properties?.cadastral_survey?.district}
                  </span>
                </div>
              </div>

              {/* Step 3: Vertical Property Mapping (Floor Stack) */}
              <div className="vertical-mapping-section">
                <div className="section-title-row">
                  <h3 className="section-title">Vertical Floor Units ({selectedBuilding.floors?.length || 0})</h3>
                  <span className="section-hint">Select a floor to focus</span>
                </div>

                <div className="floor-stack">
                  {[...(selectedBuilding.floors || [])]
                    .sort((a, b) => (b.properties?.floor_index ?? 0) - (a.properties?.floor_index ?? 0))
                    .map((floor) => {
                      const fp = floor.properties || {};
                      const isBasement = fp.floor_index < 0;
                      const isFocused = selectedFloor?.properties?.ulpin === fp.ulpin || selectedFloor?.ulpin === fp.ulpin;

                      return (
                        <div
                          key={fp.ulpin}
                          className={`floor-row ${isFocused ? "focused" : ""}`}
                          onClick={() => setSelectedFloor(fp)}
                        >
                          <div className="floor-indicator-group">
                            <span
                              className="floor-color-swatch"
                              style={{ backgroundColor: fp.color || "#3E8ED0" }}
                            />
                            <span className={`floor-tag ${isBasement ? "basement" : ""}`}>
                              {isBasement ? `B${Math.abs(fp.floor_index)}` : `F${fp.floor_index}`}
                            </span>
                          </div>

                          <div className="floor-info-group">
                            <div className="floor-name">{fp.floor_name || `Floor ${fp.floor_index}`}</div>
                            <div className="floor-ulpin-sub">{fp.ulpin}</div>
                          </div>

                          <div className="floor-elevation-badge">
                            {fp.z_min}m – {fp.z_max}m
                          </div>
                        </div>
                      );
                    })}
                </div>
              </div>

              {/* Focused Floor Detail Card */}
              {selectedFloor && (
                <div className="focused-floor-card">
                  <div className="focused-header">
                    <span className="focused-kicker">FOCUSED UNIT DETAIL</span>
                    <h4 className="focused-ulpin">{selectedFloor.ulpin || selectedFloor.properties?.ulpin}</h4>
                  </div>
                  <div className="focused-grid">
                    <div className="focused-item">
                      <span className="lbl">Elevation Slice:</span>
                      <span className="val">{selectedFloor.z_min ?? selectedFloor.properties?.z_min}m to {selectedFloor.z_max ?? selectedFloor.properties?.z_max}m</span>
                    </div>
                    <div className="focused-item">
                      <span className="lbl">Unit Floor Area:</span>
                      <span className="val">{selectedFloor.area_sqm ?? selectedFloor.properties?.area_sqm ?? "820"} m²</span>
                    </div>
                    <div className="focused-item">
                      <span className="lbl">Ownership / Title:</span>
                      <span className="val">{selectedFloor.legal_status ?? selectedFloor.properties?.legal_status ?? "Registered Freehold"}</span>
                    </div>
                    <div className="focused-item">
                      <span className="lbl">Property Tax:</span>
                      <span className="val highlight-green">{selectedFloor.tax_clearance ?? selectedFloor.properties?.tax_clearance ?? "Paid (2025-26)"}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
