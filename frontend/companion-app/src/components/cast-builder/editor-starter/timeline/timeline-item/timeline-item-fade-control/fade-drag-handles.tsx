import {memo, useMemo} from 'react';
import {clsx} from '../../../utils/clsx';

export const FADE_HANDLE_WIDTH = 6;
const HANDLE_HEIGHT = 10;

interface FadeDragHandleProps {
	onPointerDown: (e: React.MouseEvent) => void;
	showHandle: boolean;
	handlePosition: number;
}

const getHandlePosition = ({
	fadeWidthPx,
	currentFadeDuration,
}: {
	fadeWidthPx: number;
	currentFadeDuration: number;
}) => {
	if (currentFadeDuration <= 0) {
		return 0;
	}

	return Math.max(0, fadeWidthPx - FADE_HANDLE_WIDTH / 2);
};

export const getHandleLeft = ({
	fadeWidthPx,
	currentFadeDuration,
	positionProperty,
	itemWidth,
}: {
	fadeWidthPx: number;
	currentFadeDuration: number;
	positionProperty: 'left' | 'right';
	itemWidth: number;
}) => {
	const handlePosition = getHandlePosition({fadeWidthPx, currentFadeDuration});

	if (positionProperty === 'left') {
		return handlePosition;
	}

	return itemWidth - handlePosition - FADE_HANDLE_WIDTH;
};

export const FadeDragHandle = memo(
	({onPointerDown, showHandle, handlePosition}: FadeDragHandleProps) => {
		const style = useMemo(
			() => ({
				left: handlePosition,
				top: 0,
				width: FADE_HANDLE_WIDTH,
				height: HANDLE_HEIGHT,
			}),
			[handlePosition],
		);

		const className = useMemo(
			() =>
				clsx(
					'absolute',
					'cursor-ew-resize',
					'rounded',
					'rounded-b-3xl',
					'bg-white/90',
					showHandle ? 'block' : 'hidden',
				),
			[showHandle],
		);

		return (
			<div className={className} style={style} onPointerDown={onPointerDown} />
		);
	},
);

FadeDragHandle.displayName = 'DragHandle';
