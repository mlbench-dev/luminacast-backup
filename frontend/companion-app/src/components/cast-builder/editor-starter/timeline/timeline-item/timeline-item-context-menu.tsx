import {useCallback, useMemo, useContext, useState} from 'react';
import {copyToClipboard} from '../../clipboard/copy-to-clipboard';
import {ContextMenuItem, ContextMenuSeparator} from '../../context-menu';
import {
	FEATURE_BRING_TO_FRONT,
	FEATURE_COPY_LAYERS,
	FEATURE_CUT_LAYERS,
	FEATURE_DUPLICATE_LAYERS,
	FEATURE_SEND_TO_BACK,
} from '../../flags';
import {EditorStarterItem} from '../../items/item-type';
import {bringToFrontOrBack} from '../../state/actions/bring-item-to-front-or-back';
import {cutItems} from '../../state/actions/cut-items';
import {duplicateItems} from '../../state/actions/duplicate-items';
import {
	useAllItems,
	useSelectedItems,
	useWriteContext,
} from '../../utils/use-context';
import {LuminacastEditorContext} from '../../luminacast-context';
import {castsApi} from '@/lib/api';
import {confirmAction} from '@/lib/swal';
import {toast} from 'sonner';

export const TimelineItemContextMenu: React.FC<{
	item: EditorStarterItem;
}> = ({item}) => {
	const {setState} = useWriteContext();
	const {selectedItems} = useSelectedItems();
	const {items: allItems} = useAllItems();
	const luminacastCtx = useContext(LuminacastEditorContext);
	const [rendering, setRendering] = useState(false);

	// determine if we should operate on multiple items:
	// - multiple items are selected AND
	// - the right-clicked item is part of that selection
	const isMultiSelection = useMemo(
		() => selectedItems.length > 1 && selectedItems.includes(item.id),
		[selectedItems, item.id],
	);

	// for copy/cut/duplicate: operate on all selected items if conditions are met,
	// otherwise operate only on the right-clicked item
	const targetItems = useMemo(() => {
		return isMultiSelection ? selectedItems.map((id) => allItems[id]) : [item];
	}, [isMultiSelection, selectedItems, item, allItems]);

	// layer ordering operations always work on the individual
	// right-clicked item, not on the selection
	const handleBringToFront = useCallback(
		(e: Event) => {
			e.stopPropagation();
			setState({
				update: (state) =>
					bringToFrontOrBack({
						state,
						itemId: item.id, // Always the right-clicked item
						position: 'front',
					}),
				commitToUndoStack: true,
			});
		},
		[item.id, setState],
	);

	const handleSendToBack = useCallback(
		(e: Event) => {
			e.stopPropagation();
			setState({
				update: (state) =>
					bringToFrontOrBack({
						state,
						itemId: item.id, // Always the right-clicked item
						position: 'back',
					}),
				commitToUndoStack: true,
			});
		},
		[item.id, setState],
	);

	const handleCopy = useCallback(
		(e: Event) => {
			e.stopPropagation();
			copyToClipboard(targetItems);
		},
		[targetItems],
	);

	const handleCut = useCallback(
		(e: Event) => {
			e.stopPropagation();
			copyToClipboard(targetItems);
			setState({
				update: (state) =>
					cutItems(
						state,
						targetItems.map((targetItem) => targetItem.id),
					),
				commitToUndoStack: true,
			});
		},
		[targetItems, setState],
	);
	const handleDuplicate = useCallback(
		(e: Event) => {
			e.stopPropagation();
			setState({
				update: (state) =>
					duplicateItems(
						state,
						targetItems.map((targetItem) => targetItem.id),
					),
				commitToUndoStack: true,
			});
		},
		[targetItems, setState],
	);

	// Phase 4.8.3 — Per-block render
	const blockId = item.metadata?.block_id as string | undefined;
	const isBondedBlock = !!blockId && !!item.metadata?.bonded;

	const handleRenderBlock = useCallback(
		async (renderAction: string) => {
			if (!luminacastCtx || !blockId) return;
			const quality = luminacastCtx.cast.quality || 'simple';
			const ok = await confirmAction({
				title: 'Re-render this block?',
				text: `The block will be re-rendered at ${quality.toUpperCase()} quality.`,
				confirmButtonText: 'Re-render',
				cancelButtonText: 'Cancel',
				icon: 'warning',
			});
			if (!ok) return;
			setRendering(true);
			try {
				const result = await castsApi.renderBlock(luminacastCtx.castId, blockId, {
					render_action: renderAction,
				});
				toast.success(`Rendering started (cost: $${(result.cost_cents / 100).toFixed(2)})`);
			} catch (err: any) {
				toast.error(err?.response?.data?.detail || 'Render failed');
			} finally {
				setRendering(false);
			}
		},
		[luminacastCtx, blockId],
	);

	const handleContextMenuPointerDown = useCallback(
		(e: React.PointerEvent<HTMLDivElement>) => {
			e.stopPropagation();
		},
		[],
	);

	return (
		<>
			{FEATURE_CUT_LAYERS ? (
				<ContextMenuItem
					className="flex items-center gap-3"
					onSelect={handleCut}
					onPointerDown={handleContextMenuPointerDown}
				>
					Cut
				</ContextMenuItem>
			) : null}
			{FEATURE_COPY_LAYERS && (
				<ContextMenuItem
					className="flex items-center gap-3"
					onSelect={handleCopy}
					onPointerDown={handleContextMenuPointerDown}
				>
					Copy
				</ContextMenuItem>
			)}
			{FEATURE_DUPLICATE_LAYERS && (
				<ContextMenuItem
					className="flex items-center gap-3"
					onSelect={handleDuplicate}
					onPointerDown={handleContextMenuPointerDown}
				>
					Duplicate
				</ContextMenuItem>
			)}
			<ContextMenuSeparator />
			{FEATURE_BRING_TO_FRONT ? (
				<ContextMenuItem
					className="flex items-center gap-3"
					onSelect={handleBringToFront}
					onPointerDown={handleContextMenuPointerDown}
				>
					Bring to front
				</ContextMenuItem>
			) : null}
			{FEATURE_SEND_TO_BACK ? (
				<ContextMenuItem
					className="flex items-center gap-3"
					onSelect={handleSendToBack}
					onPointerDown={handleContextMenuPointerDown}
				>
					Send to back
				</ContextMenuItem>
			) : null}
			{/* Phase 4.8.3 — Per-block render actions */}
			{isBondedBlock && luminacastCtx && (
				<>
					<ContextMenuSeparator />
					<ContextMenuItem
						className="flex items-center gap-3"
						disabled={rendering}
						onSelect={() => handleRenderBlock('full')}
						onPointerDown={handleContextMenuPointerDown}
					>
						{rendering ? 'Rendering...' : 'Render this block'}
					</ContextMenuItem>
					<ContextMenuItem
						className="flex items-center gap-3"
						disabled={rendering}
						onSelect={() => handleRenderBlock('re_gesture')}
						onPointerDown={handleContextMenuPointerDown}
					>
						Re-render with new gestures
					</ContextMenuItem>
					<ContextMenuItem
						className="flex items-center gap-3"
						disabled={rendering}
						onSelect={() => handleRenderBlock('swap_angle')}
						onPointerDown={handleContextMenuPointerDown}
					>
						Swap avatar angle
					</ContextMenuItem>
				</>
			)}
		</>
	);
};
