import { useEffect, useState } from "react";

/**
 * Zernio OAuth callback landing page.
 *
 * Opened in a popup by the Connect-Platform flow. Once Zernio finishes the
 * OAuth round-trip with the social platform (TikTok, Instagram, ...), it
 * redirects the popup here with `?platform=...&status=ok` (or `error=...`).
 *
 * We postMessage the result back to the parent window so the Publish page
 * can refresh its profiles list, then auto-close after a short delay.
 */
export default function ZernioCallback() {
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const platform = params.get("platform") || "";
    const status = params.get("status") || "ok";
    const error = params.get("error") || null;

    try {
      if (window.opener && !window.opener.closed) {
        window.opener.postMessage(
          { type: "zernio-connected", platform, status, error },
          window.location.origin,
        );
      }
    } catch {
      // Swallow — different-origin opener can throw on access. Best-effort.
    }

    setClosing(true);
    const t = setTimeout(() => {
      try {
        window.close();
      } catch {
        // Some browsers refuse to close non-script-opened windows; show
        // the success card so the user can close it manually.
      }
    }, 1200);
    return () => clearTimeout(t);
  }, []);

  const params = new URLSearchParams(window.location.search);
  const platform = params.get("platform") || "platform";
  const error = params.get("error");

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0a0a0a",
        color: "#fafafa",
        fontFamily: "ui-sans-serif, system-ui",
        padding: "24px",
      }}
    >
      <div
        style={{
          maxWidth: 360,
          textAlign: "center",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 12,
          padding: "32px 24px",
          background: "rgba(255,255,255,0.03)",
        }}
      >
        <div style={{ fontSize: 28, marginBottom: 12 }}>
          {error ? "⚠️" : "✅"}
        </div>
        <h1 style={{ fontSize: 18, fontWeight: 600, marginBottom: 8 }}>
          {error ? "Could not connect" : `Connected ${platform}`}
        </h1>
        <p style={{ fontSize: 13, color: "rgba(255,255,255,0.6)" }}>
          {error
            ? error
            : closing
            ? "This window will close in a moment."
            : "Wrapping up…"}
        </p>
      </div>
    </div>
  );
}
