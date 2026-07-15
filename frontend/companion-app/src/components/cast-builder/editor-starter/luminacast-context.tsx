/**
 * LuminacastEditorContext — provides cast-level data (castId, blocks)
 * to components deep inside the Editor Starter tree (e.g. BlockScriptEditor).
 *
 * The Editor Starter's own context tree doesn't know about Luminacast casts,
 * so we layer this context on top via the LuminacastEditor wrapper.
 */
import { createContext, useContext } from "react";
import type { Cast } from "@/lib/types";

export interface LuminacastEditorContextValue {
  castId: string;
  cast: Cast;
}

export const LuminacastEditorContext =
  createContext<LuminacastEditorContextValue | null>(null);

export function useLuminacastEditor(): LuminacastEditorContextValue {
  const ctx = useContext(LuminacastEditorContext);
  if (!ctx) {
    throw new Error(
      "useLuminacastEditor must be used within a <LuminacastEditorContext.Provider>",
    );
  }
  return ctx;
}
