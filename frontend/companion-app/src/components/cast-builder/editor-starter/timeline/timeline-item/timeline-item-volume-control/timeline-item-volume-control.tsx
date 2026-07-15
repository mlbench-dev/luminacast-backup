import React, {useCallback, useEffect, useRef, useState} from 'react';

import {createPortal} from 'react-dom';
import {interpolate} from 'remotion';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../../../force-specific-cursor';
import {AudioItem} from '../../../items/audio/audio-item-type';
import {EditorStarterItem} from '../../../items/item-type';
import {VideoItem} from '../../../items/video/video-item-type';
import {changeItem} from '../../../state/actions/change-item';
import {MAX_VOLUME_DB, MIN_VOLUME_DB} from '../../../utils/decibels';
import {useWriteContext} from '../../../utils/use-context';
import {VolumeLevelIndicator} from './volume-level-indicator';
import {VolumeLine} from './volume-line';

function VolumeControlLine({
	volume,
	onVolumeChange,
}: {
	volume: number;
	onVolumeChange: (volume: number, commitToUndoStack: boolean) => void;
}) {
	const [isDragging, setIsDragging] = useState(false);
	const [cursorPosition, setCursorPosition] = useState({x: 0, y: 0});
	const containerRef = useRef<HTMLDivElement>(null);

	// Convert y position (0 to 100%) to volume (0 to 1)
	const getVolumeFromYPosition = (yPercent: number) => {
		// 0% = 1, 100% = 0
		return interpolate(
			1 - yPercent / 100,
			[0, 1],
			[MIN_VOLUME_DB, MAX_VOLUME_DB],
		);
	};

	const handleMouseDown = (e: React.MouseEvent) => {
		e.preventDefault();
		e.stopPropagation();
		forceSpecificCursor('ns-resize');
		setIsDragging(true);

		// Set initial cursor position relative to container
		if (containerRef.current) {
			setCursorPosition({x: e.clientX, y: e.clientY});
		}
	};

	const processPointerEvent = useCallback(
		(e: MouseEvent, commitToUndoStack: boolean) => {
			if (!isDragging || !containerRef.current) return;

			const rect = containerRef.current.getBoundingClientRect();
			const rawY = e.clientY - rect.top;

			// Constrain y position within the container bounds
			const y = Math.max(0, Math.min(rect.height, rawY));

			const yPercent = Math.max(0, Math.min(100, (y / rect.height) * 100));
			const newVolume = getVolumeFromYPosition(yPercent);

			// Update cursor position with constrained values
			setCursorPosition({x: e.clientX, y: e.clientY});

			onVolumeChange(
				Math.max(MIN_VOLUME_DB, Math.min(MAX_VOLUME_DB, newVolume)),
				commitToUndoStack,
			);
		},
		[isDragging, containerRef, onVolumeChange],
	);

	const handlePointerMove = useCallback(
		(e: MouseEvent) => {
			processPointerEvent(e, false);
		},
		[processPointerEvent],
	);

	const handlePointerUp = useCallback(
		(e: MouseEvent) => {
			processPointerEvent(e, true);
			stopForcingSpecificCursor();
			setIsDragging(false);
		},
		[processPointerEvent],
	);

	useEffect(() => {
		if (isDragging) {
			document.addEventListener('pointermove', handlePointerMove);
			document.addEventListener('pointerup', handlePointerUp);

			return () => {
				document.removeEventListener('pointermove', handlePointerMove);
				document.removeEventListener('pointerup', handlePointerUp);
			};
		}
	}, [isDragging, handlePointerMove, handlePointerUp]);

	return (
		<>
			<div ref={containerRef} className="pointer-events-auto absolute inset-0">
				<VolumeLine volume={volume} onMouseDown={handleMouseDown} />
				{isDragging
					? createPortal(
							<VolumeLevelIndicator
								volume={volume}
								cursorPosition={cursorPosition}
							/>,
							document.body,
						)
					: null}
			</div>
		</>
	);
}

export function TimelineItemVolumeControl({
	item,
}: {
	item: AudioItem | VideoItem;
}) {
	const {setState} = useWriteContext();

	const volume = item.decibelAdjustment;

	const handleVolumeChange = useCallback(
		(newVolume: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, item.id, (prevItem): EditorStarterItem => {
						if (prevItem.type !== 'audio' && prevItem.type !== 'video') {
							throw new Error('Item is not audio or video');
						}

						if (prevItem.decibelAdjustment === newVolume) {
							return prevItem;
						}

						return {
							...prevItem,
							decibelAdjustment: newVolume,
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, item.id],
	);

	return (
		<VolumeControlLine volume={volume} onVolumeChange={handleVolumeChange} />
	);
}
