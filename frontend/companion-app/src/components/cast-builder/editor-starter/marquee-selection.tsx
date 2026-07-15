import React, {useMemo} from 'react';

export type MarqueeSelection = {
	start: {
		x: number;
		y: number;
	};
	end: {
		x: number;
		y: number;
	};
};

export const MarqueeSelection: React.FC<{
	selection: MarqueeSelection;
}> = ({selection}) => {
	const style = useMemo((): React.CSSProperties => {
		return {
			left: selection.start.x,
			top: selection.start.y,
			width: selection.end.x - selection.start.x,
			height: selection.end.y - selection.start.y,
			position: 'absolute',
			border: '1px solid var(--color-editor-starter-accent)',
			backgroundColor: 'rgba(11, 132, 243, 0.1)',
		};
	}, [selection.end.x, selection.end.y, selection.start.x, selection.start.y]);

	return <div style={style}></div>;
};
