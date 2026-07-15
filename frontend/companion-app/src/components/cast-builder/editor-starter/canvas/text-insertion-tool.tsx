import {PlayerRef} from '@remotion/player';
import {useCallback, useContext} from 'react';
import {AbsoluteFill, useVideoConfig} from 'remotion';
import {EditModeContext} from '../edit-mode';
import {createTextItem} from '../items/text/create-text-item';
import {PreviewSizeContext} from '../preview-size';
import {addItem} from '../state/actions/add-item';
import {markTextAsEditing} from '../state/actions/text-item-editing';
import {calculateScale} from '../utils/calculate-canvas-transformation';
import {useWriteContext} from '../utils/use-context';
import {CanvasSizeContext} from './canvas-size';

export const TextInsertionTool = ({
	playerRef,
}: {
	playerRef: React.RefObject<PlayerRef | null>;
}) => {
	const {setState} = useWriteContext();
	const {width, height} = useVideoConfig();
	const {size} = useContext(PreviewSizeContext);
	const canvasSize = useContext(CanvasSizeContext);

	const {setEditMode} = useContext(EditModeContext);

	const handleCreateText = useCallback(
		async (event: React.PointerEvent<HTMLDivElement>) => {
			event.preventDefault();
			event.stopPropagation();

			if (!canvasSize) {
				throw new Error('Could not find canvas size');
			}

			const scale = calculateScale({
				canvasSize: canvasSize,
				compositionWidth: width,
				compositionHeight: height,
				previewSize: size.size,
			});

			const container = playerRef.current?.getContainerNode();
			if (!container) {
				throw new Error('Could not find container');
			}
			const containerLeft = container.getBoundingClientRect().left;
			const containerTop = container.getBoundingClientRect().top;

			const xOnCanvas = (event.clientX - containerLeft) / scale;
			const yOnCanvas = (event.clientY - containerTop) / scale;

			const text = 'Text';

			const item = await createTextItem({
				xOnCanvas,
				yOnCanvas,
				from: playerRef.current?.getCurrentFrame() ?? 0,
				text,
				align: 'center',
			});

			setState({
				update: (state) => {
					const newState = addItem({
						state,
						item,
						select: true,
						position: {type: 'front'},
					});
					return markTextAsEditing({state: newState, itemId: item.id});
				},
				commitToUndoStack: true,
			});
			setEditMode('select');
		},
		[canvasSize, height, playerRef, setEditMode, setState, size.size, width],
	);

	return <AbsoluteFill onPointerDown={handleCreateText}></AbsoluteFill>;
};
