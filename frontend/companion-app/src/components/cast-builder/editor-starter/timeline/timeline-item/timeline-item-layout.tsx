import React, {useMemo, useRef} from 'react';
import {Link2} from 'lucide-react';
import {EditorStarterItem} from '../../items/item-type';
import {isBondedHighlight} from '../../utils/bonded-pair';
import {clsx} from '../../utils/clsx';
import {useAllItems, useSelectedItems} from '../../utils/use-context';
import {useItemDrag} from '../utils/drag/use-timeline-item-drag';

export const TIMELINE_ITEM_BORDER_WIDTH = 1;

export function TimelineItemContainer({
	children,
	isSelected,
	item,
}: {
	children: React.ReactNode;
	isSelected: boolean;
	item: EditorStarterItem;
}) {
	const timelineItemRef = useRef<HTMLDivElement>(null);

	const {onPointerDown, onClick} = useItemDrag({
		draggedItem: item,
	});

	// FIX 5 (bonded tracks) ── when this item is the bonded partner of
	// the currently-selected item, paint a softer accent ring so the user
	// sees that the two halves move together.
	const {selectedItems} = useSelectedItems();
	const {items: allItems} = useAllItems();
	const bondedHighlight = useMemo(() => {
		return isBondedHighlight({
			item,
			selectedItems,
			allItems,
		});
	}, [item, selectedItems, allItems]);

	const style = useMemo(() => {
		return {
			borderWidth: TIMELINE_ITEM_BORDER_WIDTH,
		};
	}, []);

	const isBonded = Boolean(
		item.metadata?.bonded || item.metadata?.bonded_pair_id,
	);

	return (
		<div
			ref={timelineItemRef}
			onPointerDown={onPointerDown}
			onClick={onClick}
			className={clsx(
				'absolute box-border h-full w-full cursor-pointer overflow-hidden rounded-sm border border-black select-none',
				isSelected && 'border-editor-starter-accent',
				bondedHighlight && !isSelected && 'border-editor-starter-accent/60 ring-1 ring-editor-starter-accent/30',
			)}
			style={style}
		>
			{isBonded ? (
				<Link2
					aria-label="Bonded video and voice"
					className="pointer-events-none absolute top-1 left-1 h-3 w-3 text-white/50 drop-shadow-[0_0_2px_rgba(0,0,0,0.8)]"
				/>
			) : null}
			{children}
		</div>
	);
}
// TODO future: collapsed bonded view (single combined row) — spec lines 863-876.
