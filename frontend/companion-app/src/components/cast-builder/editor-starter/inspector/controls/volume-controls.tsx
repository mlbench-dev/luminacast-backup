import React, {memo, useCallback} from 'react';
import {AudioItem} from '../../items/audio/audio-item-type';
import {VideoItem} from '../../items/video/video-item-type';
import {Slider} from '../../slider';
import {changeItem} from '../../state/actions/change-item';
import {decibelToGain, MAX_VOLUME_DB, MIN_VOLUME_DB} from '../../utils/decibels';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';

const VolumeControlsUnmemoized: React.FC<{
	decibelAdjustment: number;
	itemId: string;
}> = ({decibelAdjustment, itemId}) => {
	const {setState} = useWriteContext();

	const setVolume = useCallback(
		(newDecibelAdjustment: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const prev = i as AudioItem | VideoItem;
						if (prev.decibelAdjustment === newDecibelAdjustment) {
							return prev;
						}
						// Preview reads decibelAdjustment directly; the render pipeline
						// (background music / sfx) reads metadata.volume as a linear
						// 0-1 gain (resolve_music_volume in cast_ffmpeg_composer.py) —
						// write both so this slider affects both, clamped to unity
						// since the render mix can't go hotter than source level.
						return {
							...prev,
							decibelAdjustment: newDecibelAdjustment,
							metadata: {
								...prev.metadata,
								volume: Math.max(0, Math.min(1, decibelToGain(newDecibelAdjustment))),
							},
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const handleSliderChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			setVolume(value, commitToUndoStack);
		},
		[setVolume],
	);

	return (
		<div>
			<InspectorSubLabel>Volume</InspectorSubLabel>
			<div className="flex w-full items-center gap-3">
				<Slider
					value={decibelAdjustment}
					onValueChange={handleSliderChange}
					min={MIN_VOLUME_DB}
					max={MAX_VOLUME_DB}
					step={0.5}
					className="flex-1"
					title={`Volume: ${decibelAdjustment.toFixed(1)} db`}
				/>
				<div className="min-w-[50px] text-right text-xs text-white/75">
					{decibelAdjustment.toFixed(1)} dB
				</div>
			</div>
		</div>
	);
};

export const VolumeControls = memo(VolumeControlsUnmemoized);
