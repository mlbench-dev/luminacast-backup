import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";

/**
 * Source guard for the Cast Builder phase header layout.
 *
 * Bug: on the Arrange (editor) phase the "Finalize & Render" button and the
 * kebab menu overflowed the right edge at 100% zoom with no way to scroll to
 * them, and the phase-pill strip got squeezed until pills were hidden.
 *
 * Fix: the header is now two rows. The phase-pill strip owns the full top row;
 * the actions sit on their own row underneath, right-aligned, and wrap among
 * themselves. The top row + cast name + strip still carry `min-w-0` so the
 * name can truncate, which also needs `min-w-0` on the CastBuilder wrappers
 * between the header and <main> (which already has min-w-0).
 */
const SRC = path.resolve(__dirname, "../../src");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");

describe("PhaseHeader — responsive header", () => {
  const src = read("components/cast-builder/PhaseHeader.tsx");

  it("outer container is a column so the actions can drop to a second row", () => {
    expect(src).toMatch(/className="flex flex-col[^"]*\bw-full\b[^"]*\bmin-w-0\b/);
  });

  it("top row fills width and can shrink", () => {
    expect(src).toMatch(/className="flex items-center[^"]*\bw-full\b[^"]*\bmin-w-0\b/);
  });

  it("cast name truncates and is NOT shrink-0", () => {
    const nameLine = src.split("\n").find((l) => l.includes("title={castName}") || l.includes('title={castName}'));
    // the <span> carrying the cast name
    const span = src.match(/<span\s+className="([^"]*truncate[^"]*)"\s*\n?\s*title=\{castName\}/);
    expect(span, "cast name span with truncate + title").toBeTruthy();
    const cls = span![1];
    expect(cls).toContain("truncate");
    expect(cls).toContain("min-w-0");
    expect(cls).not.toContain("shrink-0");
  });

  it("phase-pill strip scrolls (hidden bar) instead of pushing siblings or clipping the last pill", () => {
    // flex-1 min-w-0 so it yields space; overflow-x-auto (not -hidden) so the
    // last pill ("Ready") stays reachable; scrollbar hidden both engines.
    expect(src).toMatch(/flex-1 min-w-0 overflow-x-auto/);
    expect(src).toContain("[scrollbar-width:none]");
    expect(src).toContain("[&::-webkit-scrollbar]:hidden");
  });

  it("keeps the meaningful pill in view and each pill un-squished", () => {
    // pills never shrink — the strip scrolls instead
    expect(src).toMatch(/data-phase-idx=\{idx\} className="[^"]*\bshrink-0\b/);
    expect(src).toMatch(/whitespace-nowrap[^"]*\bshrink-0\b|\bshrink-0\b[^"]*whitespace-nowrap/);
    // Ready pill (last) is scrolled into view whenever a render exists, else
    // the current step
    expect(src).toContain("scrollIntoView");
    expect(src).toMatch(/renderState === "none" \? currentIdx : PHASES\.length - 1/);
  });

  it("actions render on their own right-aligned row that can wrap", () => {
    const actionsBlock = src.match(/\{actions && \(([\s\S]*?)\)\}/);
    expect(actionsBlock, "actions render block").toBeTruthy();
    expect(actionsBlock![1]).toMatch(/flex flex-wrap items-center justify-end/);
    expect(actionsBlock![1]).toContain("w-full");
  });
});

describe("CastBuilder — header wrappers propagate shrink", () => {
  const cb = read("pages/cast-builder/CastBuilder.tsx");

  it("the header flex row has min-w-0", () => {
    expect(cb).toMatch(/\{!\(isNew && phase === "setup"\)[\s\S]{0,120}?<div className="flex items-start min-w-0">/);
  });

  it("the PhaseHeader wrapper has min-w-0", () => {
    expect(cb).toMatch(/<div className="flex-1 min-w-0">\s*\n\s*<PhaseHeader/);
  });
});
