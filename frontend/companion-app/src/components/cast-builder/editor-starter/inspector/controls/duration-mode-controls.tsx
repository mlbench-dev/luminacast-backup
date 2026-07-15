/**
 * DurationModeControls — Phase 2.7.2
 *
 * Radio buttons for visual element duration mode: crop / stretch / keep.
 * Controls how the element responds to bonded audio duration changes (Phase 2.7.3).
 */
import React, {memo, useCallback} from 'react';
import {changeItem} from '../../state/actions/change-item';
import {useWriteContext} from '../../utils/use-context';
import {InspectorSubLabel} from '../components/inspector-label';

type DurationMode = 'crop' | 'stretch' | 'keep';

const OPTIONS: { value: DurationMode; label: string; description: string }[] = [
	{
		value: 'crop',
		label: 'Match audio (crop)',
		description: 'Trim end to match audio duration',
	},
	{
		value: 'stretch',
		label: 'Match audio (stretch)',
		description: 'Adjust playback rate to match audio',
	},
	{
		value: 'keep',
		label: 'Keep original',
		description: 'Leave duration unchanged',
	},
];

const DurationModeControlsUnmemoized: React.FC<{
	itemId: string;
	currentMode: DurationMode;
	/** Whether this is a video element (stretch is only meaningful for video) */
	isVideo?: boolean;
}> = ({itemId, currentMode, isVideo}) => {
	const {setState} = useWriteContext();

	const handleChange = useCallback(
		(newMode: DurationMode) => {
			setState({
				update: (state) =>
					changeItem(state, itemId, (i) => ({
						...i,
						metadata: {
							...i.metadata,
							duration_mode: newMode,
						},
					})),
				commitToUndoStack: true,
			});
		},
		[setState, itemId],
	);

	return (
		<div>
			<InspectorSubLabel>Duration Mode</InspectorSubLabel>
			<div className="flex flex-col gap-1">
				{OPTIONS.map((opt) => {
					// Stretch is only meaningful for video — hide for images
					if (opt.value === 'stretch' && !isVideo) return null;
					return (
						<label
							key={opt.value}
							className="flex cursor-pointer items-start gap-2 rounded px-1 py-0.5 text-xs text-neutral-300 hover:bg-white/5"
						>
							<input
								type="radio"
								name={`duration-mode-${itemId}`}
								value={opt.value}
								checked={currentMode === opt.value}
								onChange={() => handleChange(opt.value)}
								className="mt-0.5 h-3.5 w-3.5 border-neutral-600 bg-transparent text-blue-500 focus:ring-0 focus:ring-offset-0"
							/>
							<div>
								<div className="font-medium">{opt.label}</div>
								<div className="text-[0.65rem] text-white/40">
									{opt.description}
								</div>
							</div>
						</label>
					);
				})}
			</div>
		</div>
	);
};

export const DurationModeControls = memo(DurationModeControlsUnmemoized);
