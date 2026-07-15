import {useMemo, useState, useCallback, useRef, useEffect} from 'react';
import type {EditorStarterItem} from '../../items/item-type';
import {useWriteContext} from '../../utils/use-context';

export function TimelineItemHeader({filename, item}: {filename: string; item?: EditorStarterItem}) {
	const {setState} = useWriteContext();
	const [editing, setEditing] = useState(false);
	const inputRef = useRef<HTMLInputElement>(null);

	const displayLabel = item?.metadata?.custom_label || filename;
	const debugId = item?.metadata?.block_id || item?.id;

	const style = useMemo(() => {
		return {
			padding: 10,
			marginLeft: 0,
			marginRight: 0,
			marginTop: -10,
			marginBottom: -10,
			textShadow: '0 0 10px black, 0 0 10px black, 1px 1px 1px black',
		};
	}, []);

	const handleDoubleClick = useCallback((e: React.MouseEvent) => {
		e.stopPropagation();
		e.preventDefault();
		setEditing(true);
	}, []);

	const commitLabel = useCallback((newLabel: string) => {
		setEditing(false);
		const trimmed = newLabel.trim();
		if (!trimmed || trimmed === displayLabel || !item) return;
		setState({
			update: (state) => {
				const existing = state.undoableState.items[item.id];
				if (!existing) return state;
				return {
					...state,
					undoableState: {
						...state.undoableState,
						items: {
							...state.undoableState.items,
							[item.id]: {
								...existing,
								metadata: { ...existing.metadata, custom_label: trimmed },
							},
						},
					},
				};
			},
			commitToUndoStack: true,
		});
	}, [item, displayLabel, setState]);

	const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
		if (e.key === 'Enter') {
			commitLabel(e.currentTarget.value);
		} else if (e.key === 'Escape') {
			setEditing(false);
		}
		e.stopPropagation();
	}, [commitLabel]);

	const handleBlur = useCallback((e: React.FocusEvent<HTMLInputElement>) => {
		commitLabel(e.currentTarget.value);
	}, [commitLabel]);

	useEffect(() => {
		if (editing && inputRef.current) {
			inputRef.current.focus();
			inputRef.current.select();
		}
	}, [editing]);

	return (
		<div
			className="pointer-events-auto absolute top-0 flex h-20 w-full items-start justify-between"
			title={debugId ? `ID: ${debugId}` : displayLabel}
		>
			{editing ? (
				<input
					ref={inputRef}
					defaultValue={displayLabel}
					onKeyDown={handleKeyDown}
					onBlur={handleBlur}
					className="mx-[10px] mt-0 w-[calc(100%-20px)] bg-black/50 text-xs font-medium text-white/90 outline-none border-b border-accent"
					style={{
						textShadow: '0 0 10px black, 0 0 10px black, 1px 1px 1px black',
					}}
				/>
			) : (
				<span
					className="truncate px-[10px] text-xs font-medium text-white/90 cursor-text"
					style={style}
					onDoubleClick={handleDoubleClick}
				>
					{displayLabel}
				</span>
			)}
		</div>
	);
}
