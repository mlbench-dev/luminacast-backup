import React from "react";
import ReactDOM from "react-dom/client";
import { initSentry } from "./lib/sentry";
import App from "./App";
import "./styles/globals.css";
import "sweetalert2/dist/sweetalert2.min.css";

initSentry();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
// cache-bust-v1
