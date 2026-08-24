import {PlayerRef} from '@remotion/player';
import * as Popover from '@radix-ui/react-popover';
import React, {useCallback, useState} from 'react';
import {MoreVertical, Trash2, Layers as LayersIcon} from 'lucide-react';
import {
	FEATURE_CANVAS_ZOOM_CONTROLS,
	FEATURE_DELETE_SHORTCUT,
	FEATURE_DOWNLOAD_STATE,
	FEATURE_LOAD_STATE,
	FEATURE_REDO_BUTTON,
	FEATURE_SAVE_BUTTON,
	FEATURE_UNDO_BUTTON,
} from '../flags';
import {CanvasZoomControls} from './canvas-zoom-controls';
import {DownloadStateButton} from './download-state-button';
import {LoadStateButton} from './load-state-button';
import {RedoButton} from './redo-button';
import {SaveButton} from './save-button';
import {TasksIndicator} from './tasks-indicator/tasks-indicator';
import {ToolSelection} from './tool-selection';
import {UndoButton} from './undo-button';
import {SafeZoneToggle} from './safe-zone-toggle';
import {useLayerPanel} from '../layer-panel-context';
import {deleteItems} from '../state/actions/delete-items';
import {useSelectedItems, useWriteContext} from '../utils/use-context';

// Caption Fix 6 — single-row toolbar with three semantic groups separated by
// thin dividers. Layout (left → right):
//
//   [Tool ▾][Undo Redo Save]  │  Safe zones  │  Layers  │  ⋯  ─── Fit ───
//   ─────────  group 1  ────     group 2 (radio-ish)  ── group 3 ──  more  zoom
//
// Group 2 (aspect-ratio guides) lives inside SafeZoneToggle and uses a filled
// pill background when active. Group 3 (view toggles) uses an accent
// underline beneath the label to distinguish toggles from radios. The red
// Delete button is gone — destructive actions live in the ⋯ kebab menu;
// Backspace / Delete on the canvas still delete the selected item.
//
// Captions and Gestures toggles were removed from group 3 — both were local
// useState with zero consumers anywhere in the codebase (confirmed: neither
// was ever passed to the canvas or read by any renderer), so clicking them
// changed their own highlighted look and nothing else. Same class of dead
// toggle as the Solo track button removed earlier.

const Divider: React.FC = () => (
	<div className="h-5 w-px shrink-0 bg-white/10" aria-hidden="true" />
);

const GroupLabel: React.FC<{
	active: boolean;
	onClick: () => void;
	title: string;
	children: React.ReactNode;
	icon?: React.ReactNode;
}> = ({active, onClick, title, children, icon}) => (
	<button
		type="button"
		role="switch"
		aria-checked={active}
		aria-pressed={active}
		onClick={onClick}
		title={title}
		className={`relative flex h-7 shrink-0 items-center gap-1 rounded px-2 text-[11px] font-medium transition-colors ${
			active
				? 'text-accent'
				: 'text-white/50 hover:bg-white/5 hover:text-white/80'
		}`}
	>
		{icon}
		{children}
		{/* Active toggle indicator: 2px accent underline, distinct from
		    Group 2 radios (which use a filled pill background). */}
		<span
			aria-hidden="true"
			className={`pointer-events-none absolute inset-x-2 -bottom-[3px] h-[2px] rounded-full bg-accent transition-opacity ${
				active ? 'opacity-100' : 'opacity-0'
			}`}
		/>
	</button>
);

const KebabMenu: React.FC = () => {
	const [open, setOpen] = useState(false);
	const {selectedItems} = useSelectedItems();
	const {setState} = useWriteContext();

	const hasSelection = selectedItems.length > 0;

	const handleDelete = useCallback(() => {
		if (!FEATURE_DELETE_SHORTCUT || !hasSelection) return;
		setState({
			update: (state) => deleteItems(state, selectedItems),
			commitToUndoStack: true,
		});
		setOpen(false);
	}, [setState, selectedItems, hasSelection]);

	return (
		<Popover.Root open={open} onOpenChange={setOpen}>
			<Popover.Trigger asChild>
				<button
					type="button"
					aria-label="More options"
					title="More options"
					className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-white/50 transition-colors hover:bg-white/5 hover:text-white/80"
				>
					<MoreVertical className="h-4 w-4" />
				</button>
			</Popover.Trigger>
			<Popover.Portal>
				<Popover.Content
					side="bottom"
					align="end"
					sideOffset={6}
					className="z-50 min-w-[160px] rounded-md border border-white/10 bg-neutral-900/95 p-1 shadow-lg backdrop-blur"
				>
					<button
						type="button"
						disabled={!hasSelection}
						onClick={handleDelete}
						className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-red-500 transition-colors hover:bg-red-500/10 ${
							hasSelection ? '' : 'opacity-50 pointer-events-none'
						}`}
					>
						<Trash2 className="h-3.5 w-3.5" />
						<span className="flex-1">Delete</span>
						<span className="text-[10px] text-white/30">Del</span>
					</button>
					{/* TODO future menu items */}
				</Popover.Content>
			</Popover.Portal>
		</Popover.Root>
	);
};

export const ActionRow: React.FC<{
	playerRef: React.RefObject<PlayerRef | null>;
}> = ({playerRef}) => {
	const {isOpen: layerPanelOpen, toggle: toggleLayerPanel} = useLayerPanel();

	return (
		<div
			role="toolbar"
			aria-label="Editor toolbar"
			className="border-b-editor-starter-border bg-editor-starter-panel flex w-full items-center gap-3 border-b px-3 py-2 whitespace-nowrap overflow-x-auto"
		>
			{/* ── Group 1: selection + history ─────────────────────────── */}
			<div className="flex shrink-0 items-center gap-1">
				<ToolSelection playerRef={playerRef} />
				<div className="flex shrink-0 overflow-hidden rounded">
					{FEATURE_UNDO_BUTTON && <UndoButton />}
					<div className="bg-editor-starter-panel w-px"></div>
					{FEATURE_REDO_BUTTON && <RedoButton />}
					<div className="bg-editor-starter-panel w-px"></div>
					{FEATURE_SAVE_BUTTON && <SaveButton />}
					{FEATURE_DOWNLOAD_STATE && (
						<>
							<div className="bg-editor-starter-panel w-px"></div>
							<DownloadStateButton />
						</>
					)}
					{FEATURE_LOAD_STATE && (
						<>
							<div className="bg-editor-starter-panel w-px"></div>
							<LoadStateButton />
						</>
					)}
				</div>
				<TasksIndicator />
			</div>

			<Divider />

			{/* ── Group 2: aspect-ratio guides (radio-style) ──────────── */}
			<SafeZoneToggle />

			<Divider />

			{/* ── Group 3: view toggles (Layers) ── */}
			<div
				role="group"
				aria-label="View toggles"
				className="flex shrink-0 items-center gap-1"
			>
				<GroupLabel
					active={layerPanelOpen}
					onClick={toggleLayerPanel}
					title="Toggle layer panel"
					icon={<LayersIcon className="h-3.5 w-3.5" />}
				>
					Layers
				</GroupLabel>
			</div>

			<Divider />

			{/* ── Kebab overflow ───────────────────────────────────────── */}
			<KebabMenu />

			{/* ── Zoom cluster (right-aligned) ─────────────────────────── */}
			<div className="ml-auto flex shrink-0 items-center">
				{FEATURE_CANVAS_ZOOM_CONTROLS ? <CanvasZoomControls /> : null}
			</div>
		</div>
	);
};
