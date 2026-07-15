import {PlayerRef} from '@remotion/player';
import React, {useCallback, useContext, useState} from 'react';
import {PreviewSize} from 'remotion';
import {toast} from 'sonner';
import {addAsset} from '../assets/add-asset';
import {getImageNameFromHtml} from '../clipboard/get-image-name-from-html';
import {parseItemsFromClipboardTextHtml} from '../clipboard/parse-items';
import {
	ContextMenu,
	ContextMenuContent,
	ContextMenuItem,
	ContextMenuTrigger,
} from '../context-menu';
import {createTextItem} from '../items/text/create-text-item';
import {MarqueeSelection} from '../marquee-selection';
import {PreviewSizeContext} from '../preview-size';
import {addItem} from '../state/actions/add-item';
import {pasteItems} from '../state/actions/paste-items';
import {
	CanvasTransformation,
	calculateScale,
} from '../utils/calculate-canvas-transformation';
import {useCanvasMarqueeSelection} from '../utils/marquee-selection/use-canvas-marquee-selection';
import {
	useCurrentStateAsRef,
	useDimensions,
	useWriteContext,
} from '../utils/use-context';
import {CanvasSizeContext} from './canvas-size';

export const EmptyCanvasContextMenuTrigger: React.FC<{
	children: React.ReactNode;
	playerRef: React.RefObject<PlayerRef | null>;
	canvasTransformation: CanvasTransformation;
	previewSize: PreviewSize;
	canvasRef: React.RefObject<HTMLDivElement | null>;
}> = ({children, playerRef, canvasTransformation, previewSize, canvasRef}) => {
	const {compositionWidth, compositionHeight} = useDimensions();
	const timelineWriteContext = useWriteContext();
	const {size} = useContext(PreviewSizeContext);
	const canvasSize = useContext(CanvasSizeContext);
	const state = useCurrentStateAsRef();
	const [contextMenuPosition, setContextMenuPosition] = useState<{
		x: number;
		y: number;
	} | null>(null);

	const {rect, onPointerDown} = useCanvasMarqueeSelection({
		playerRef,
		canvasTransformation,
		previewSize,
		canvasRef,
	});

	const handleContextMenu = useCallback((event: React.MouseEvent) => {
		setContextMenuPosition({x: event.clientX, y: event.clientY});
	}, []);

	const handlePasteHere = useCallback(async () => {
		const container = playerRef.current?.getContainerNode();
		if (!canvasSize || !container || !contextMenuPosition) {
			throw new Error('Canvas size is not set');
		}

		const scale = calculateScale({
			canvasSize,
			compositionWidth,
			compositionHeight,
			previewSize: size.size,
		});

		const containerLeft = container.getBoundingClientRect().left;
		const containerTop = container.getBoundingClientRect().top;

		const xOnCanvas = (contextMenuPosition.x - containerLeft) / scale;
		const yOnCanvas = (contextMenuPosition.y - containerTop) / scale;

		try {
			const clipboard = await navigator.clipboard.read();
			for (const item of clipboard) {
				// Order is important, image also has text/html
				if (item.types.includes('image/png')) {
					const blob = await item.getType('image/png');
					const html = await item.getType('text/html');
					const htmlText = await html.text();
					const imageName = getImageNameFromHtml(htmlText);

					if (!imageName) {
						throw new Error('No image filename found');
					}

					await addAsset({
						compositionHeight,
						compositionWidth,
						fps: state.current.undoableState.fps,
						tracks: state.current.undoableState.tracks,
						timelineWriteContext: timelineWriteContext,
						playerRef,
						dropPosition: null,
						file: blob,
						filename: imageName,
					});
				} else if (item.types.includes('text/html')) {
					const blob = await item.getType('text/html');
					const text = await blob.text();
					const parsedAsItems = parseItemsFromClipboardTextHtml(text);
					if (parsedAsItems === null) {
						continue;
					}
					timelineWriteContext.setState({
						commitToUndoStack: true,
						update: (prevState) =>
							pasteItems({
								state: prevState,
								copiedItems: parsedAsItems,
								from: playerRef.current?.getCurrentFrame() ?? 0,
								position: {
									x: xOnCanvas,
									y: yOnCanvas,
								},
							}),
					});
				} else if (item.types.includes('text/plain')) {
					const blob = await item.getType('text/plain');
					const text = await blob.text();
					if (text.trim() === '') {
						continue;
					}
					const parsedItems = parseItemsFromClipboardTextHtml(text);
					if (parsedItems === null) {
						const textItem = await createTextItem({
							xOnCanvas: xOnCanvas,
							yOnCanvas: yOnCanvas,
							from: playerRef.current?.getCurrentFrame() ?? 0,
							text,
							align: 'center',
						});
						timelineWriteContext.setState({
							update: (prevState) =>
								addItem({
									state: prevState,
									item: textItem,
									select: true,
									position: {type: 'front'},
								}),
							commitToUndoStack: true,
						});
						continue;
					}
					timelineWriteContext.setState({
						commitToUndoStack: true,
						update: (prevState) =>
							pasteItems({
								state: prevState,
								copiedItems: parsedItems,
								from: playerRef.current?.getCurrentFrame() ?? 0,
								position: {
									x: xOnCanvas,
									y: yOnCanvas,
								},
							}),
					});
				}
			}
		} catch {
			toast.error('Not allowed to read clipboard');
		}
	}, [
		playerRef,
		contextMenuPosition,
		canvasSize,
		compositionWidth,
		compositionHeight,
		size.size,
		timelineWriteContext,
		state,
	]);

	const handleContextMenuPointerDown = useCallback(
		(e: React.PointerEvent<HTMLDivElement>) => {
			e.stopPropagation();
		},
		[],
	);

	return (
		<>
			<ContextMenu>
				<ContextMenuTrigger>
					<div
						onPointerDown={onPointerDown}
						onContextMenu={handleContextMenu}
						style={{display: 'contents'}}
					>
						{children}
					</div>
				</ContextMenuTrigger>
				<ContextMenuContent>
					<ContextMenuItem
						className="flex items-center gap-3"
						onSelect={handlePasteHere}
						onPointerDown={handleContextMenuPointerDown}
					>
						Paste here
					</ContextMenuItem>
				</ContextMenuContent>
			</ContextMenu>
			{rect ? <MarqueeSelection selection={rect} /> : null}
		</>
	);
};
