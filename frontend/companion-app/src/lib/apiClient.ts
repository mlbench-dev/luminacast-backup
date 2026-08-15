import axios from "axios";
import { Sentry } from "@/lib/sentry";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";
console.log("BASE_URL", BASE_URL);
export const api = axios.create({
  baseURL: BASE_URL,
  headers: { "Content-Type": "application/json" },
});

// Lightweight UUID v4 generator. crypto.randomUUID is widely available but
// fall back for older browsers / older WebViews still in the wild.
function uuidv4(): string {
  try {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
  } catch {}
  // RFC 4122 v4 fallback.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Per-tab session id. Persists across reloads via sessionStorage so
// audit-log can group everything from one tab without conflating tabs.
function getSessionId(): string {
  try {
    let id = sessionStorage.getItem("luminacast-session-id");
    if (!id) {
      id = uuidv4();
      sessionStorage.setItem("luminacast-session-id", id);
    }
    return id;
  } catch {
    return uuidv4();
  }
}

const MUTATING_METHODS = new Set(["post", "put", "patch", "delete"]);

// Attach JWT token + audit-log session/action IDs.
// Ensure token is always fresh from localStorage on each request.
api.interceptors.request.use((config) => {
  if (!config.headers["Authorization"]) {
    try {
      const raw = localStorage.getItem("luminacast-auth");
      if (raw) {
        const parsed = JSON.parse(raw);
        const token = parsed?.state?.token;
        if (token) {
          config.headers["Authorization"] = "Bearer " + token;
        }
      }
    } catch {}
  }

  // Tag every request with the per-tab session id so the audit log can
  // group actions; tag every mutation with a fresh action id so the UI
  // can correlate optimistic updates to their server-side event.
  try {
    config.headers["X-Session-Id"] = getSessionId();
    const method = (config.method || "get").toLowerCase();
    if (MUTATING_METHODS.has(method)) {
      config.headers["X-Action-Id"] = uuidv4();
    }
  } catch {}

  return config;
});

// Capture 5xx API errors to Sentry
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (axios.isAxiosError(error) && error.response && error.response.status >= 500) {
      Sentry.captureException(error, {
        extra: {
          url: error.config?.url,
          method: error.config?.method,
          status: error.response.status,
          data: error.response.data,
        },
      });
    }
    return Promise.reject(error);
  },
);

/**
 * Extracts a display-friendly string from an axios error's response body.
 * FastAPI returns `detail` as a plain string for custom HTTPExceptions, but
 * as an array of Pydantic error objects ({type, loc, msg, ...}) for raw
 * request-validation failures (422s) — rendering that array directly as JSX
 * throws "Objects are not valid as a React child".
 */
export function extractErrorMessage(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback;
  const detail = err.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((d) => (typeof d?.msg === "string" ? d.msg : String(d))).join(" ");
  }
  return fallback;
}

export function setAuthToken(token: string | null) {
  if (token) {
    api.defaults.headers.common["Authorization"] = `Bearer ${token}`;
  } else {
    delete api.defaults.headers.common["Authorization"];
  }
}
