import {makeCircle, makePie} from '@remotion/shapes';
import {useMemo} from 'react';
import {CheckIcon} from '../../icons/check';
import {DeleteIcon} from '../../icons/delete';

export type ProgressType =
	| {
			type: 'numeric-progress';
			progress: number;
	  }
	| {
			type: 'success';
	  }
	| {
			type: 'working';
	  }
	| {
			type: 'error';
	  };

export const CircularTaskIndicator: React.FC<{
	progress: ProgressType;
}> = ({progress}) => {
	const innerRadius =
		progress.type === 'success' || progress.type === 'error' ? 10 : 7;
	const outerRadius = 10;

	const style: React.CSSProperties = useMemo(() => {
		return {
			width: outerRadius * 2,
			height: outerRadius * 2,
			display: 'flex',
			alignItems: 'center',
			justifyContent: 'center',
		};
	}, []);

	const innerStyle: React.CSSProperties = useMemo(() => {
		return {
			width: innerRadius * 2,
			height: innerRadius * 2,
		};
	}, [innerRadius]);

	const circlePath = useMemo(() => {
		return makeCircle({radius: outerRadius}).path;
	}, []);

	const piePath = useMemo(() => {
		if (progress.type === 'numeric-progress') {
			return makePie({radius: innerRadius, progress: progress.progress}).path;
		}

		if (progress.type === 'success' || progress.type === 'error') {
			return makePie({radius: innerRadius, progress: 1}).path;
		}

		return null;
	}, [progress, innerRadius]);

	return (
		<div className="relative" style={style}>
			{progress.type === 'numeric-progress' ? (
				<svg
					viewBox={`0 0 ${outerRadius * 2} ${outerRadius * 2}`}
					className="absolute z-0 overflow-visible stroke-3 opacity-50"
				>
					<path
						d={circlePath}
						className="stroke-neutral-300"
						fill="none"
						strokeWidth={1}
					/>
				</svg>
			) : null}
			{progress.type === 'working' ? (
				<svg
					viewBox={`0 0 ${outerRadius * 2} ${outerRadius * 2}`}
					className="absolute z-0 animate-spin overflow-visible stroke-3 opacity-50"
				>
					<path
						d={circlePath}
						className="stroke-neutral-300"
						fill="none"
						strokeWidth={2}
						strokeDasharray={`${outerRadius * Math.PI} ${outerRadius * Math.PI}`}
					/>
				</svg>
			) : null}
			{piePath ? (
				<svg
					viewBox={`0 0 ${innerRadius * 2} ${innerRadius * 2}`}
					style={innerStyle}
					className="absolute z-0 overflow-visible stroke-3"
				>
					<path d={piePath} className="fill-neutral-300" strokeWidth={1}></path>
				</svg>
			) : null}
			<div style={style} className="absolute">
				{progress.type === 'success' ? (
					<CheckIcon className="text-editor-starter-panel size-3" />
				) : null}
				{progress.type === 'error' ? (
					<DeleteIcon className="text-editor-starter-panel size-4" />
				) : null}
			</div>
		</div>
	);
};
