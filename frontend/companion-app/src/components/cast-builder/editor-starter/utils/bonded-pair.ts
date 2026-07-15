/**
 * Bonded-pair utilities (Caption UX Fix 5).
 *
 * Bonded items are video + audio (occasionally text/captions) that belong to
 * the same logical block. The mapping layer sets:
 *   item.metadata.bonded = true
 *   item.metadata.paired_audio_element_id = <id>   // on the video
 *   item.metadata.paired_video_element_id = <id>   // on the audio
 *   item.metadata.block_id = <block id>
 *
 * These helpers let timeline-edit code mirror trim / move / select
 * operations from one half of a bonded pair onto the other half so the
 * user perceives them as a single unit.
 */
import type {EditorStarterItem} from '../items/item-type';

/**
 * Return the id of the partner item bonded to the given one, or null when
 * the item is not bonded or its partner cannot be resolved.
 */
export function getBondedPartnerId(item: EditorStarterItem | undefined): string | null {
	if (!item || !item.metadata?.bonded) return null;
	return (
		item.metadata.paired_audio_element_id ||
		item.metadata.paired_video_element_id ||
		null
	);
}

/**
 * Same as getBondedPartnerId but resolves through the items map and skips
 * partners that no longer exist (split, deleted, undone). Returns the
 * partner item itself.
 *
 * Resolution order:
 *   1. The directional `paired_audio_element_id` / `paired_video_element_id`
 *      pointer (set by the mapper, preferred when present).
 *   2. Fallback to scanning for any other item that shares the same
 *      `metadata.bonded_pair_id` (FIX 5 — robust if the pointer is stale).
 */
export function getBondedPartner(
	item: EditorStarterItem | undefined,
	items: Record<string, EditorStarterItem>,
): EditorStarterItem | null {
	if (!item) return null;
	const partnerId = getBondedPartnerId(item);
	if (partnerId && items[partnerId]) return items[partnerId];
	const pairId = item.metadata?.bonded_pair_id;
	if (!pairId) return null;
	for (const candidate of Object.values(items)) {
		if (candidate.id === item.id) continue;
		if (candidate.metadata?.bonded_pair_id === pairId) return candidate;
	}
	return null;
}

/**
 * For the given list of items that just had their `from` /
 * `durationInFrames` changed, return an updated map that ALSO mirrors the
 * change onto each item's bonded partner.
 *
 * Mirroring rules:
 *   - The partner's `from` is forced to match the changed item's `from`.
 *   - The partner's `durationInFrames` is forced to match.
 *   - The partner is only updated if it actually exists in `items`.
 *   - If both halves of a pair appear in `changedIds`, we still mirror;
 *     they will simply land on the same final value.
 */
export function mirrorTimingsToBondedPartners({
	items,
	changedIds,
}: {
	items: Record<string, EditorStarterItem>;
	changedIds: string[];
}): Record<string, EditorStarterItem> {
	let result: Record<string, EditorStarterItem> | null = null;
	const changedSet = new Set(changedIds);
	for (const id of changedIds) {
		const it = items[id];
		if (!it) continue;
		const partner = getBondedPartner(it, items);
		if (!partner) {
			// Sibling missing (e.g. one of the pair was deleted). Spec says
			// no-op the bond logic and warn.
			if (it.metadata?.bonded_pair_id || it.metadata?.bonded) {
				if (typeof console !== 'undefined') {
					console.warn(
						`[bonded] sibling missing for item ${id}; skipping mirror`,
					);
				}
			}
			continue;
		}
		// If both halves are in the same change set (multi-select), skip
		// double-application — the explicit change for the partner wins.
		if (changedSet.has(partner.id)) continue;
		if (
			partner.from === it.from &&
			partner.durationInFrames === it.durationInFrames
		) {
			continue;
		}
		if (result === null) result = {...items};
		result[partner.id] = {
			...partner,
			from: it.from,
			durationInFrames: it.durationInFrames,
		};
	}
	return result ?? items;
}

/**
 * Selection-driven bonded-highlight test. Returns true when this item is
 * NOT itself selected, but its bonded partner IS — so the item should
 * render with a soft highlight ring telling the user "I move/resize with
 * the selected one".
 */
export function isBondedHighlight({
	item,
	selectedItems,
	allItems,
}: {
	item: EditorStarterItem;
	selectedItems: string[];
	allItems: Record<string, EditorStarterItem>;
}): boolean {
	if (selectedItems.includes(item.id)) return false;
	const partner = getBondedPartner(item, allItems);
	if (!partner) return false;
	return selectedItems.includes(partner.id);
}
