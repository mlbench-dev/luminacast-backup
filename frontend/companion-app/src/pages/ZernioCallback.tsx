import { useEffect, useRef, useState } from "react";
import { socialApi } from "@/lib/api";

/**
 * Close the current (popup) window as reliably as browsers allow.
 *
 * A plain `window.close()` is silently refused by some browsers once the
 * window has navigated through a different-origin page — which the OAuth
 * consent screen always is, so a DECLINED authorization (this window:
 * ours → Zernio → Google/TikTok consent → back) can be exactly the case
 * that trips the restriction, even though a successful one goes through
 * the same number of hops. `window.open("", "_self")` re-asserts the
 * current script as the window's "owner" before closing, which is a
 * known workaround for that restriction. We verify with `window.closed`
 * and retry a couple of times since the effect isn't always immediate.
 */
function attemptClose(retriesLeft = 3) {
  try {
    window.close();
  } catch {
    // ignore — checked via window.closed below
  }
  if (window.closed || retriesLeft <= 0) return;
  setTimeout(() => {
    if (window.closed) return;
    try {
      window.open("", "_self");
      window.close();
    } catch {
      // ignore — will retry or give up below
    }
    if (!window.closed) {
      setTimeout(() => attemptClose(retriesLeft - 1), 300);
    }
  }, 150);
}

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
  const rawError = new URLSearchParams(window.location.search).get("error");
  // Starts as the generic code from the URL (e.g. "connection_failed") and
  // gets replaced with Zernio's real explanation once the lookup below
  // resolves, if one is found — see getConnectError's docstring.
  const [displayError, setDisplayError] = useState<string | null>(rawError);
  // React.StrictMode double-invokes effects in dev (mount → cleanup →
  // mount) to surface missing-cleanup bugs. postMessage has no "undo", so
  // without this guard the double-invoke sends the connected notification
  // twice and the Publish page shows the "Account connected" toast twice
  // for a single real connection.
  const sentRef = useRef(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // Guard the ENTIRE operation (lookup + send + timer) as one atomic
    // unit, checked synchronously before any async work starts. StrictMode's
    // dev-mode double-invoke runs this effect body twice; a previous version
    // of this guard only wrapped the postMessage send while an independent
    // "cancelled" flag guarded the rest, which raced two separate
    // getConnectError lookups against each other across the two
    // invocations — the wrong one's plain "connection_failed" ended up
    // winning the race to actually fire postMessage. Guarding at the top
    // means only ONE invocation ever does any work at all — the other
    // returns immediately — so there is no second flow left to race against.
    if (sentRef.current) return;
    sentRef.current = true;

    (async () => {
      const params = new URLSearchParams(window.location.search);
      const platform = params.get("platform") || "";
      const status = params.get("status") || "ok";
      let error = params.get("error") || null;

      // The redirect only ever carries a generic code — look up Zernio's
      // activity log for the real reason (e.g. "no YouTube channel on
      // this Google account") so both this page and the toast on the
      // opener show something actionable. Best-effort: on any failure
      // (network, no matching log, request timeout) we just keep the
      // generic code.
      if (error && platform) {
        try {
          const res = await socialApi.getConnectError(platform);
          if (res.detail) error = res.detail;
        } catch {
          // fall back to the generic code
        }
      }
      setDisplayError(error);

      try {
        if (window.opener && !window.opener.closed) {
          // targetOrigin must match the OPENER's origin, not this
          // callback page's own — postMessage silently drops the
          // message otherwise. This page's origin varies (whatever
          // connect_platform's redirect_uri pointed at) while the
          // opener can be a different origin during local/staging
          // testing, so there's no single fixed value that's ever
          // guaranteed correct here. "*" is safe: the payload is just a
          // platform name + status, nothing sensitive.
          window.opener.postMessage(
            { type: "zernio-connected", platform, status, error },
            "*",
          );
        }
      } catch {
        // Swallow — different-origin opener can throw on access. Best-effort.
      }

      setClosing(true);
      timeoutRef.current = setTimeout(() => {
        attemptClose();
      }, 1200);
    })();

    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const platform = new URLSearchParams(window.location.search).get("platform") || "platform";
  const error = displayError;

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
