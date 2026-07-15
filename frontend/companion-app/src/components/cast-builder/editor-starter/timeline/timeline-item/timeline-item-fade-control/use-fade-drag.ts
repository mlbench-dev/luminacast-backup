import {useCallback, useMemo, useRef, useState} from 'react';
import {MAX_FADE_DURATION_SECONDS} from '../../../constants';
import {
	forceSpecificCursor,
	stopForcingSpecificCursor,
} from '../../../force-specific-cursor';
import {EditorStarterItem} from '../../../items/item-type';
import {changeItem} from '../../../state/actions/change-item';
import {
	useCurrentStateAsRef,
	useFps,
	useWriteContext,
} from '../../../utils/use-context';
import {REQUIRED_WIDTH_BETWEEN_FADE_HANDLES} from '../timeline-item-extend-handles/clamp-fade-duration';
import {FADE_HANDLE_WIDTH} from './fade-drag-handles';
import {getFadeValue} from './get-fade-value';
import {FadeMediaType, FadeType} from './item-fade-handles';

const clampFadeDuration = (duration: number, maxDuration: number): number => {
	return Math.max(0, Math.min(maxDuration, duration));
};

type FadeDragPosition = {
	elementX: number;
	elementY: number;
	initialFadeValue: number;
};

const getFadeProperty = (type: FadeType, fadeType: FadeMediaType) => {
	return type === 'in'
		? fadeType === 'audio'
			? 'audioFadeInDurationInSeconds'
			: 'fadeInDurationInSeconds'
		: fadeType === 'audio'
			? 'audioFadeOutDurationInSeconds'
			: 'fadeOutDurationInSeconds';
};

export const useFadeDrag = ({
	item,
	type,
	width,
	fadeType,
}: {
	item: EditorStarterItem;
	type: FadeType;
	width: number;
	fadeType: FadeMediaType;
}) => {
	const fadeProperty = getFadeProperty(type, fadeType);
	const otherFadeProperty = getFadeProperty(
		type === 'in' ? 'out' : 'in',
		fadeType,
	);
	const {setState} = useWriteContext();
	const [position, setPosition] = useState<FadeDragPosition | null>(null);
	const {fps} = useFps();
	const stateAsRef = useCurrentStateAsRef();
	const element = useRef<HTMLDivElement>(null);
	const [isDragging, setIsDragging] = useState(false);

	const handleMouseDown = useCallback(
		(e: React.MouseEvent) => {
			e.preventDefault();
			e.stopPropagation();
			forceSpecificCursor('ew-resize');
			setIsDragging(true);
			const current = element.current;
			if (!current) {
				throw new Error('Element is not mounted');
			}

			const initialItem = stateAsRef.current.undoableState.items[
				item.id
			] as EditorStarterItem;

			if (
				(fadeProperty === 'fadeInDurationInSeconds' ||
					fadeProperty === 'fadeOutDurationInSeconds') &&
				initialItem.type === 'audio'
			) {
				throw new Error('Invalid fade property for audio item');
			}

			const initialFadeValue = getFadeValue({item: initialItem, fadeProperty});
			const initialItemDuration = initialItem.durationInFrames;
			let newFadeValue = initialFadeValue;

			const initialClientX = e.clientX;

			const {x, y} = current?.getBoundingClientRect() ?? {x: 0, y: 0};

			setPosition({
				elementX: x,
				elementY: y,
				initialFadeValue,
			});

			const cleanup = () => {
				document.removeEventListener('pointermove', handlePointerMove);
				document.removeEventListener('pointerup', handlePointerUp);
			};

			const handlePointerMove = (evt: MouseEvent) => {
				let deltaX = evt.clientX - initialClientX;

				// For fade out, invert the delta since we're dragging from right to left
				if (type === 'out') {
					deltaX = -deltaX;
				}

				const itemDurationInSeconds = initialItemDuration / fps;
				const deltaFadeDuration = (deltaX / width) * itemDurationInSeconds;

				// this also takes into account the other fade duration to avoid overlapping
				const baseMaxAllowedDuration =
					itemDurationInSeconds -
					getFadeValue({item: initialItem, fadeProperty: otherFadeProperty}) -
					((FADE_HANDLE_WIDTH + REQUIRED_WIDTH_BETWEEN_FADE_HANDLES) / width) *
						itemDurationInSeconds;

				const maxAllowedDuration = Math.min(
					MAX_FADE_DURATION_SECONDS,
					baseMaxAllowedDuration,
				);

				newFadeValue = clampFadeDuration(
					initialFadeValue + deltaFadeDuration,
					maxAllowedDuration,
				);

				if (!element.current) {
					throw new Error('Element is not mounted');
				}

				setState({
					update: (state) => {
						return changeItem(state, item.id, (prevItem) => ({
							...prevItem,
							[fadeProperty]: newFadeValue,
						}));
					},
					commitToUndoStack: false,
				});
			};

			const handlePointerUp = (evt: PointerEvent) => {
				stopForcingSpecificCursor();
				setIsDragging(false);
				cleanup();
				evt.stopPropagation();
				setState({
					update: (state) => {
						return changeItem(state, item.id, (prevItem) => {
							if (
								prevItem.type !== 'audio' &&
								prevItem.type !== 'video' &&
								prevItem.type !== 'image' &&
								prevItem.type !== 'text' &&
								prevItem.type !== 'solid' &&
								prevItem.type !== 'gif'
							) {
								throw new Error('expected fadeable item');
							}

							if (
								getFadeValue({item: prevItem, fadeProperty}) === newFadeValue
							) {
								return prevItem;
							}

							return {
								...prevItem,
								[fadeProperty]: newFadeValue,
							};
						});
					},
					commitToUndoStack: true,
				});
			};

			document.addEventListener('pointermove', handlePointerMove);
			document.addEventListener('pointerup', handlePointerUp);
		},
		[
			element,
			fadeProperty,
			fps,
			item.id,
			setState,
			stateAsRef,
			otherFadeProperty,
			type,
			width,
		],
	);

	return useMemo(
		() => ({
			handleMouseDown,
			cursorPosition: position,
			ref: element,
			isDragging,
		}),
		[handleMouseDown, position, isDragging],
	);
};

export type FadeDrag = ReturnType<typeof useFadeDrag>;
