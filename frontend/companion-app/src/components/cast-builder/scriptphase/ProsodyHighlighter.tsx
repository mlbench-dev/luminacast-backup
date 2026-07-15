import { useMemo } from "react";

/**
 * ProsodyHighlighter — renders an overlay layer that mirrors the textarea
 * content but colours the Fish Speech S2 prosody tags so the user can
 * see at a glance where they put (excited), [pause] etc.
 *
 * Why an overlay instead of a contenteditable div: the existing
 * ScriptPhase has a lot of behavior wired to the textarea (autosave,
 * undo/redo, gesture autocomplete). Swapping it for a contenteditable
 * would be a far bigger refactor. Instead we render a transparent-text
 * textarea and stack a styled <pre> behind it that matches its
 * geometry — selection caret stays in the textarea, colours come from
 * the overlay.
 *
 * This component is purely presentational. The parent decides where to
 * place it (absolutely positioned over the textarea).
 */

const PROSODY_TAGS: Array<{ pattern: RegExp; className: string }> = [
  { pattern: /\(excited\)/gi,       className: "text-yellow-300 font-medium" },
  { pattern: /\(super happy\)/gi,   className: "text-orange-300 font-medium" },
  { pattern: /\(casual\)/gi,        className: "text-emerald-300 font-medium" },
  { pattern: /\(whispering\)/gi,    className: "text-purple-300 font-medium" },
  { pattern: /\(laughing\)/gi,      className: "text-pink-300 font-medium" },
  { pattern: /\(sighing\)/gi,       className: "text-sky-300 font-medium" },
  { pattern: /\[pause\]/gi,         className: "text-cyan-300 font-medium" },
  // Legacy gesture markers — render in green so users on older scripts
  // still see them highlighted, even though we don't generate new ones.
  { pattern: /\[gesture:[^\]]+\]/gi,className: "text-lime-400/70 italic" },
];

type Token = { text: string; className: string | null };

/**
 * Tokenize a string into runs of (text, className). Earliest match wins.
 * We scan once per regex; results are sorted by start index and
 * non-overlapping ranges are kept.
 */
function tokenize(text: string): Token[] {
  type Hit = { start: number; end: number; className: string };
  const hits: Hit[] = [];
  for (const { pattern, className } of PROSODY_TAGS) {
    pattern.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = pattern.exec(text)) !== null) {
      hits.push({ start: m.index, end: m.index + m[0].length, className });
      // Guard against zero-width matches (shouldn't happen with our patterns).
      if (m.index === pattern.lastIndex) pattern.lastIndex++;
    }
  }
  hits.sort((a, b) => a.start - b.start);

  // Drop overlapping hits — keep the earliest starting one.
  const filtered: Hit[] = [];
  let lastEnd = -1;
  for (const h of hits) {
    if (h.start >= lastEnd) {
      filtered.push(h);
      lastEnd = h.end;
    }
  }

  // Build the final token list, interleaving plain text between hits.
  const tokens: Token[] = [];
  let cursor = 0;
  for (const h of filtered) {
    if (h.start > cursor) tokens.push({ text: text.slice(cursor, h.start), className: null });
    tokens.push({ text: text.slice(h.start, h.end), className: h.className });
    cursor = h.end;
  }
  if (cursor < text.length) tokens.push({ text: text.slice(cursor), className: null });
  return tokens;
}

export function ProsodyHighlighter({ text, className }: { text: string; className?: string }) {
  const tokens = useMemo(() => tokenize(text), [text]);
  return (
    // The overlay renders the SAME text the textarea contains, but with
    // prosody tags coloured. The textarea itself uses `text-transparent`
    // so only the caret + selection are drawn from the textarea — every
    // visible glyph comes from THIS overlay. Geometry must match the
    // textarea exactly: same font size (text-sm), line-height (1.6),
    // padding (px-3 py-2), and word-break/wrap rules. pre-wrap preserves
    // whitespace + wraps long words like the textarea does.
    <pre
      aria-hidden="true"
      className={
        "pointer-events-none absolute inset-0 px-3 py-2 m-0 text-sm leading-[1.6] font-sans text-white/85 whitespace-pre-wrap break-words overflow-hidden " +
        (className ?? "")
      }
    >
      {tokens.map((t, i) =>
        t.className ? (
          <span key={i} className={t.className}>
            {t.text}
          </span>
        ) : (
          <span key={i}>{t.text}</span>
        ),
      )}
      {/* Trailing newline so an end-of-text caret has a place to land
          (textareas reserve a phantom row for trailing newlines). */}
      {"\n"}
    </pre>
  );
}

/** Strip prosody tags + whitespace for word counting. */
export function stripProsody(text: string): string {
  let stripped = text;
  for (const { pattern } of PROSODY_TAGS) {
    pattern.lastIndex = 0;
    stripped = stripped.replace(pattern, " ");
  }
  return stripped.replace(/\s+/g, " ").trim();
}
