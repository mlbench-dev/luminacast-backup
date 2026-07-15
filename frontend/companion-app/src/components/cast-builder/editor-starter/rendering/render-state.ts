/**
 * Source: Remotion Editor Starter v4.0.433, file: rendering/render-state.ts
 * Adapted for Luminacast: Stubbed — Luminacast uses its own render pipeline (FinalizingPhase + Celery).
 * Full type exports are preserved for type compatibility with task indicator and state action files.
 */

export type CodecOption = "h264" | "vp8";

export type RenderingTaskState =
  | { type: "render-initiated" }
  | { type: "done"; outputFile: string; outputSizeInBytes: number; doneAt: number }
  | { type: "in-progress"; overallProgress: number }
  | { type: "error"; error: string };

export type RenderingTask = {
  id: string;
  outputName: string;
  status: RenderingTaskState;
  codec: CodecOption;
  durationInSeconds: number;
  startedAt: number;
  type: "rendering";
};
