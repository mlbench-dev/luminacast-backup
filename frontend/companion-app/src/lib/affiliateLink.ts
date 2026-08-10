// Amazon only pays out commission on clicks that carry your Associates
// tracking tag (the `tag=` query param) — a bare product link earns nothing,
// no matter how accurate the commission-rate math shown elsewhere is.
// This appends the creator's connected tag at the point a link is actually
// rendered, so outbound "buy" links are real, attributed affiliate links.

export function buildOutboundProductUrl(
  url: string | null | undefined,
  amazonAssociateTag?: string | null,
): string {
  if (!url) return "";
  if (!amazonAssociateTag) return url;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return url;
  }
  if (!parsed.hostname.includes("amazon.")) return url;
  parsed.searchParams.set("tag", amazonAssociateTag);
  return parsed.toString();
}
