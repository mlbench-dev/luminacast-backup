import React from 'react';
import {
	FEATURE_RENDERING,
	FEATURE_SWAP_COMPOSITION_DIMENSIONS_BUTTON,
} from '../flags';
import {RotateRight} from '../icons/rotate-right';
import {RenderControls} from '../rendering/render-controls';
import {renderFrame} from '../utils/render-frame';
import {
	useDimensions,
	useFps,
	useTimelineContext,
	useWriteContext,
} from '../utils/use-context';
import {InspectorIconButton} from './components/inspector-icon-button';
import {InspectorLabel} from './components/inspector-label';
import {
	InspectorDivider,
	InspectorSection,
} from './components/inspector-section';
import {
	NumberControl,
	NumberControlUpdateHandler,
} from './controls/number-controls';

export const CompositionInspector: React.FC = () => {
	const {durationInFrames} = useTimelineContext();
	const {fps} = useFps();
	const {compositionWidth, compositionHeight} = useDimensions();
	const {setState} = useWriteContext();

	const swapDimensions = React.useCallback(() => {
		setState({
			update: (state) => {
				if (
					state.undoableState.compositionWidth ===
					state.undoableState.compositionHeight
				) {
					return state;
				}

				return {
					...state,
					undoableState: {
						...state.undoableState,
						compositionWidth: state.undoableState.compositionHeight,
						compositionHeight: state.undoableState.compositionWidth,
					},
				};
			},
			commitToUndoStack: true,
		});
	}, [setState]);

	const setCompositionHeight: NumberControlUpdateHandler = React.useCallback(
		({num, commitToUndoStack}) => {
			setState({
				update: (state) => {
					if (num === state.undoableState.compositionHeight) {
						return state;
					}

					return {
						...state,
						undoableState: {
							...state.undoableState,
							compositionHeight: num,
						},
					};
				},
				commitToUndoStack,
			});
		},
		[setState],
	);

	const setCompositionWidth: NumberControlUpdateHandler = React.useCallback(
		({num, commitToUndoStack}) => {
			setState({
				update: (state) => {
					if (num === state.undoableState.compositionWidth) {
						return state;
					}

					return {
						...state,
						undoableState: {
							...state.undoableState,
							compositionWidth: num,
						},
					};
				},
				commitToUndoStack,
			});
		},
		[setState],
	);

	return (
		<div>
			<InspectorSection>
				<InspectorLabel>Canvas</InspectorLabel>
				<div className="h-2"></div>
				<div className="flex flex-row gap-2">
					<div className="flex flex-1">
						<NumberControl
							label="W"
							setValue={setCompositionWidth}
							value={compositionWidth}
							min={2}
							max={null}
							step={2}
							accessibilityLabel="Width"
						/>
					</div>
					<div className="flex flex-1">
						<NumberControl
							label="H"
							setValue={setCompositionHeight}
							value={compositionHeight}
							min={2}
							max={null}
							step={2}
							accessibilityLabel="Height"
						/>
					</div>
					{FEATURE_SWAP_COMPOSITION_DIMENSIONS_BUTTON && (
						<div className="editor-starter-field hover:border-transparent">
							<InspectorIconButton
								className="flex h-full w-8 flex-1 items-center justify-center"
								onClick={swapDimensions}
								aria-label="Swap Dimensions"
							>
								<RotateRight height={12} width={12}></RotateRight>
							</InspectorIconButton>
						</div>
					)}
				</div>
			</InspectorSection>
			<InspectorDivider />
			<InspectorSection>
				<InspectorLabel>Duration</InspectorLabel>
				<div className="h-2"></div>
				<div className="text-xs text-neutral-300">
					{renderFrame(durationInFrames, fps)}
				</div>
			</InspectorSection>
			<InspectorDivider />
			{FEATURE_RENDERING ? <RenderControls /> : null}
		</div>
	);
};
