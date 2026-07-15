import {useMemo} from 'react';
import {MIN_VOLUME_DB} from '../../../utils/decibels';
import {Z_INDEX_VOLUME_TOOLTIP} from '../../../z-indices';

interface VolumeLevelIndicatorProps {
	volume: number;
	cursorPosition: {x: number; y: number};
}

const height = 30;

export function VolumeLevelIndicator({
	volume,
	cursorPosition,
}: VolumeLevelIndicatorProps) {
	const style: React.CSSProperties = useMemo(() => {
		const left = Math.max(0, Math.min(cursorPosition.x + 25));
		const top = Math.max(0, Math.min(cursorPosition.y - height / 2));
		return {
			left: left,
			top: top,
			fontWeight: 500,
			height,
			zIndex: Z_INDEX_VOLUME_TOOLTIP,
		};
	}, [cursorPosition]);

	const label = useMemo(() => {
		if (volume === MIN_VOLUME_DB) {
			return '-∞ dB';
		}

		if (volume > 0) {
			return '+' + volume.toFixed(1) + ' dB';
		}
		return volume.toFixed(1) + ' dB';
	}, [volume]);

	return (
		<div
			className="bg-opacity-90 pointer-events-none fixed flex items-center justify-center rounded bg-black px-3 text-xs text-neutral-300 tabular-nums"
			style={style}
		>
			{label}
		</div>
	);
}
