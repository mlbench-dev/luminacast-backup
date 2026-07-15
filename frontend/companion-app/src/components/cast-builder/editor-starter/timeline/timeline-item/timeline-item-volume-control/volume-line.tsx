import {interpolate} from 'remotion';
import {MAX_VOLUME_DB, MIN_VOLUME_DB} from '../../../utils/decibels';

interface VolumeLineProps {
	volume: number;
	onMouseDown: (e: React.MouseEvent) => void;
}

export function VolumeLine({volume, onMouseDown}: VolumeLineProps) {
	// Convert volume (0 to 1) to y position (0 to 100%)
	const getYPositionFromVolume = (vol: number) => {
		// 1 = 0%, 0 = 100% from top
		return 100 - interpolate(vol, [MIN_VOLUME_DB, MAX_VOLUME_DB], [0, 1]) * 100;
	};

	const yPosition = getYPositionFromVolume(volume);

	return (
		<div
			className="absolute h-px w-full bg-white opacity-20"
			style={{
				top: `${yPosition}%`,
				cursor: 'ns-resize',
			}}
			onPointerDown={onMouseDown}
		/>
	);
}
