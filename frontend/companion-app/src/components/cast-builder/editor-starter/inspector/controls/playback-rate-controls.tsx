import React, {memo, useCallback} from 'react';
import {Slider} from '../../slider';
import {changeItem} from '../../state/actions/change-item';
import {useAssetFromAssetId, useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';
import {getMaxSafeDurationInFrames} from './playback-rate-collision-detection';

const MIN_PLAYBACK_RATE = 0.25;
const MAX_PLAYBACK_RATE = 5;

const PlaybackRateControlsUnmemoized: React.FC<{
	playbackRate: number;
	itemId: string;
	assetId: string;
}> = ({playbackRate, itemId, assetId}) => {
	const {setState} = useWriteContext();
	const asset = useAssetFromAssetId(assetId);

	const setPlaybackRate = useCallback(
		(newPlaybackRate: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						if (i.type === 'video' && asset.type === 'video') {
							const maxDurationBasedOnAsset = Math.floor(
								((asset.durationInSeconds - i.videoStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							if (i.playbackRate === newPlaybackRate) {
								return i;
							}

							const idealNewDuration = Math.floor(
								((asset.durationInSeconds - i.videoStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							// If increasing playback rate (making faster), cap to current duration
							// If decreasing playback rate (making slower), allow expansion up to collision point
							const safeDuration =
								newPlaybackRate > i.playbackRate
									? Math.min(i.durationInFrames, idealNewDuration)
									: getMaxSafeDurationInFrames({
											item: i,
											tracks: state.undoableState.tracks,
											items: state.undoableState.items,
											newDuration: idealNewDuration,
										});

							const finalDuration = Math.min(
								safeDuration,
								maxDurationBasedOnAsset,
							);

							return {
								...i,
								playbackRate: newPlaybackRate,
								durationInFrames: finalDuration,
							};
						}
						if (i.type === 'audio' && asset.type === 'audio') {
							const maxDurationBasedOnAsset = Math.floor(
								((asset.durationInSeconds - i.audioStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							if (i.playbackRate === newPlaybackRate) {
								return i;
							}

							// Calculate the new duration based on the new playback rate
							const idealNewDuration = Math.floor(
								((asset.durationInSeconds - i.audioStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							// If increasing playback rate (making faster), cap to current duration
							// If decreasing playback rate (making slower), allow expansion up to collision point
							const safeDuration =
								newPlaybackRate > i.playbackRate
									? Math.min(i.durationInFrames, idealNewDuration)
									: getMaxSafeDurationInFrames({
											item: i,
											tracks: state.undoableState.tracks,
											items: state.undoableState.items,
											newDuration: idealNewDuration,
										});

							// Still respect the asset's maximum duration
							const finalDuration = Math.min(
								safeDuration,
								maxDurationBasedOnAsset,
							);

							return {
								...i,
								playbackRate: newPlaybackRate,
								durationInFrames: finalDuration,
							};
						}

						if (i.type === 'gif' && asset.type === 'gif') {
							const maxDurationBasedOnAsset = Math.floor(
								((asset.durationInSeconds - i.gifStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							if (i.playbackRate === newPlaybackRate) {
								return i;
							}

							const idealNewDuration = Math.floor(
								((asset.durationInSeconds - i.gifStartFromInSeconds) /
									newPlaybackRate) *
									state.undoableState.fps,
							);

							// If increasing playback rate (making faster), cap to current duration
							// If decreasing playback rate (making slower), allow expansion up to collision point
							const safeDuration =
								newPlaybackRate > i.playbackRate
									? Math.min(i.durationInFrames, idealNewDuration)
									: getMaxSafeDurationInFrames({
											item: i,
											tracks: state.undoableState.tracks,
											items: state.undoableState.items,
											newDuration: idealNewDuration,
										});

							const finalDuration = Math.min(
								safeDuration,
								maxDurationBasedOnAsset,
							);

							return {
								...i,
								playbackRate: newPlaybackRate,
								durationInFrames: finalDuration,
							};
						}

						throw new Error(
							`Playback rate control not implemented for this item type: ${i.type}`,
						);
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId, asset],
	);

	const handleSliderChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			const newPlaybackRate = value / 100;
			setPlaybackRate(newPlaybackRate, commitToUndoStack);
		},
		[setPlaybackRate],
	);

	const playbackRatePercent = Math.round(playbackRate * 100);

	return (
		<div>
			<InspectorSubLabel>Playback Rate</InspectorSubLabel>
			<div className="flex w-full items-center gap-3">
				<Slider
					value={playbackRatePercent}
					onValueChange={handleSliderChange}
					min={MIN_PLAYBACK_RATE * 100}
					max={MAX_PLAYBACK_RATE * 100}
					step={5}
					className="flex-1"
					title={`Playback Rate: ${playbackRate.toFixed(2)}x`}
				/>
				<div className="min-w-[40px] text-right text-xs text-white/75">
					{playbackRate.toFixed(2)}x
				</div>
			</div>
		</div>
	);
};

export const PlaybackRateControls = memo(PlaybackRateControlsUnmemoized);
