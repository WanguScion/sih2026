import React from "react";
import { createRoot } from "react-dom/client";

// Ensure Cesium static assets (Workers, Assets, Widgets) resolve correctly
window.CESIUM_BASE_URL = "/";

import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")).render(<App />);
