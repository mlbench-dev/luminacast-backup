/**
 * Source: Remotion Editor Starter v4.0.433, file: utils/server-env.ts
 * Adapted for Luminacast: Stubbed — Luminacast does not use Remotion Lambda or server-side env config.
 */
export const requireServerEnv = (): never => {
  throw new Error("Server environment not available — Luminacast uses its own backend pipeline.");
};
