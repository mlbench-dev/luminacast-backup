/**
 * Collapse a long, multi-sentence message (e.g. Zernio's full connect-error
 * explanation, which reads like a support article with step-by-step
 * instructions) into a single toast-sized line — the first sentence, hard
 * capped so an unusually long "sentence" still can't blow out the toast.
 *
 * The full message is still shown in full elsewhere (the OAuth callback
 * popup has room for it); this is specifically for toast descriptions.
 */
export function oneLineSummary(text: string, maxLen = 140): string {
  const trimmed = text.trim();
  const firstSentenceEnd = trimmed.search(/[.!?](\s|$)/);
  const firstSentence =
    firstSentenceEnd === -1 ? trimmed : trimmed.slice(0, firstSentenceEnd + 1);
  if (firstSentence.length <= maxLen) return firstSentence;
  return `${firstSentence.slice(0, maxLen - 1).trimEnd()}…`;
}
