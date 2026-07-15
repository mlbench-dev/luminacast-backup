import {memo, useLayoutEffect, useRef} from 'react';
import {TimelineTickMark} from '../utils/use-ticks';
import {TICKS_HEIGHT} from './constants';

const tickStyle: React.CSSProperties = {
	fontSize: 10,
	height: TICKS_HEIGHT,
};

export const TickHeader: React.FC<{
	tick: TimelineTickMark;
}> = memo(({tick}) => {
	const tickRef = useRef<HTMLDivElement>(null);

	// modify width directly without re-creating the element
	useLayoutEffect(() => {
		if (tickRef.current) {
			tickRef.current.style.width = tick.width + 'px';
			tickRef.current.style.minWidth = tick.width + 'px';
		}
	}, [tick.width]);

	return (
		<div className="relative" ref={tickRef}>
			<div
				className="bg-editor-starter-panel flex items-start truncate border-l-[1px] border-l-white/5 pt-3 pl-1 text-slate-400"
				style={tickStyle}
			>
				{tick.label}
			</div>
		</div>
	);
});
