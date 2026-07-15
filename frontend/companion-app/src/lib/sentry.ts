import * as Sentry from "@sentry/react";

export function initSentry() {
  const dsn = import.meta.env.VITE_SENTRY_DSN;
  if (!dsn) return;

  Sentry.init({
    dsn,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({ maskAllText: false, blockAllMedia: false }),
    ],
    tracesSampleRate: 0.3,
    replaysSessionSampleRate: 0.05,
    replaysOnErrorSampleRate: 0.5,
    environment: import.meta.env.MODE || "production",
  });
}

export { Sentry };
