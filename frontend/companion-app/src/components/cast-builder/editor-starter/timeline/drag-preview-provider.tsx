import React, {createContext, useContext, useState} from 'react';
import {TrackInsertions} from './utils/drag/types';
import {SnapPoint} from './utils/snap-points';

export type PreviewPosition = {
	id: string;
	trackIndex: number;
	from: number;
	durationInFrames: number;
};

export type DragPreviewState = {
	positions: PreviewPosition[];
	trackInsertions: TrackInsertions | null;
	itemsBeingDragged: string[];
	snapPoint: SnapPoint | null;
};

const DragPreviewContext = createContext<DragPreviewState | null>(null);
const DragPreviewSetterContext = createContext<
	((state: DragPreviewState | null) => void) | null
>(null);

export const DragPreviewProvider: React.FC<{children: React.ReactNode}> = ({
	children,
}) => {
	const [previewState, setPreviewState] = useState<DragPreviewState | null>(
		null,
	);

	return (
		<DragPreviewSetterContext.Provider value={setPreviewState}>
			<DragPreviewContext.Provider value={previewState}>
				{children}
			</DragPreviewContext.Provider>
		</DragPreviewSetterContext.Provider>
	);
};

export const useDragPreview = () => {
	const ctx = useContext(DragPreviewContext);
	return ctx;
};

export const useDragPreviewSetter = () => {
	const ctx = useContext(DragPreviewSetterContext);
	if (!ctx) {
		throw new Error(
			'useDragPreviewSetter must be used within DragPreviewProvider',
		);
	}
	return ctx;
};
