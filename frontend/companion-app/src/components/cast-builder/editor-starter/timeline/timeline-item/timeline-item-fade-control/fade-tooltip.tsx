import {memo, useMemo} from 'react';
import {Z_INDEX_FADE_TOOLTIP} from '../../../z-indices';
import {FadeType} from './item-fade-handles';

interface FadeIndicatorProps {
	type: FadeType;
	duration: number;
	cursorPosition: {
		elementX: number;
		elementY: number;
		initialFadeValue: number;
	};
	handlePosition: number;
}

const FADE_INDICATOR_OFFSET = {x: 0, y: -32};
const FADE_INDICATOR_WIDTH = 120;

export const FadeTooltip = memo(
	({type, duration, cursorPosition, handlePosition}: FadeIndicatorProps) => {
		const style = useMemo(
			() => ({
				left:
					Math.max(
						0,
						handlePosition + cursorPosition.elementX + FADE_INDICATOR_OFFSET.x,
					) -
					FADE_INDICATOR_WIDTH / 2,
				top: Math.max(0, cursorPosition.elementY + FADE_INDICATOR_OFFSET.y),
				fontWeight: 500,
				height: 30,
				width: FADE_INDICATOR_WIDTH,
				zIndex: Z_INDEX_FADE_TOOLTIP,
			}),
			[cursorPosition.elementY, handlePosition, cursorPosition.elementX],
		);

		return (
			<div
				className="bg-opacity-90 pointer-events-none fixed flex items-center justify-center rounded bg-black px-1 text-xs text-neutral-300 tabular-nums"
				style={style}
			>
				Fade {type === 'in' ? 'In' : 'Out'}: {duration.toFixed(1)}s
			</div>
		);
	},
);

FadeTooltip.displayName = 'FadeIndicator';
