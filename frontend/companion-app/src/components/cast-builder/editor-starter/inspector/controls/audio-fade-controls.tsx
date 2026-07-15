import React, {memo, useCallback} from 'react';
import {MAX_FADE_DURATION_SECONDS} from '../../constants';
import {AudioItem} from '../../items/audio/audio-item-type';
import {Slider} from '../../slider';
import {changeItem} from '../../state/actions/change-item';
import {useFps, useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';

const AudioFadeControlsUnmemoized: React.FC<{
	fadeInDuration: number;
	fadeOutDuration: number;
	itemId: string;
	durationInFrames: number;
}> = ({fadeInDuration, fadeOutDuration, itemId, durationInFrames}) => {
	const {fps} = useFps();
	const {setState} = useWriteContext();

	const setFadeInDuration = useCallback(
		(newFadeInDuration: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const prev = i as AudioItem;
						if (prev.audioFadeInDurationInSeconds === newFadeInDuration) {
							return prev;
						}
						return {
							...prev,
							audioFadeInDurationInSeconds: newFadeInDuration,
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const setFadeOutDuration = useCallback(
		(newFadeOutDuration: number, commitToUndoStack: boolean) => {
			setState({
				update: (state) => {
					return changeItem(state, itemId, (i) => {
						const prev = i as AudioItem;
						if (prev.audioFadeOutDurationInSeconds === newFadeOutDuration) {
							return prev;
						}
						return {
							...prev,
							audioFadeOutDurationInSeconds: newFadeOutDuration,
						};
					});
				},
				commitToUndoStack,
			});
		},
		[setState, itemId],
	);

	const handleFadeInChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			setFadeInDuration(value, commitToUndoStack);
		},
		[setFadeInDuration],
	);

	const handleFadeOutChange = useCallback(
		(value: number, commitToUndoStack: boolean) => {
			setFadeOutDuration(value, commitToUndoStack);
		},
		[setFadeOutDuration],
	);

	// Calculate max fade duration based on item duration and fade constraints
	const clipDurationInSeconds = durationInFrames / fps;

	// Each fade cannot overlap the other and together they cannot exceed clip duration
	// Keep a minimal gap of one frame to prevent visual overlap
	const minGap = 1 / fps;
	const maxFadeInDuration = Math.max(
		0,
		Math.min(
			MAX_FADE_DURATION_SECONDS,
			clipDurationInSeconds - fadeOutDuration - minGap,
		),
	);

	const maxFadeOutDuration = Math.max(
		0,
		Math.min(
			MAX_FADE_DURATION_SECONDS,
			clipDurationInSeconds - fadeInDuration - minGap,
		),
	);

	return (
		<div className="space-y-4">
			<div>
				<InspectorSubLabel>Fade In</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={fadeInDuration}
						onValueChange={handleFadeInChange}
						min={0}
						max={maxFadeInDuration}
						step={0.1}
						className="flex-1"
						title={`Fade In: ${fadeInDuration.toFixed(1)}s`}
					/>
					<div className="min-w-[50px] text-right text-xs text-white/75">
						{fadeInDuration.toFixed(1)}s
					</div>
				</div>
			</div>
			<div>
				<InspectorSubLabel>Fade Out</InspectorSubLabel>
				<div className="flex w-full items-center gap-3">
					<Slider
						value={fadeOutDuration}
						onValueChange={handleFadeOutChange}
						min={0}
						max={maxFadeOutDuration}
						step={0.1}
						className="flex-1"
						title={`Fade Out: ${fadeOutDuration.toFixed(1)}s`}
					/>
					<div className="min-w-[50px] text-right text-xs text-white/75">
						{fadeOutDuration.toFixed(1)}s
					</div>
				</div>
			</div>
		</div>
	);
};

export const AudioFadeControls = memo(AudioFadeControlsUnmemoized);
