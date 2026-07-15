import React from 'react';
import {fadeEasingFunction} from '../../../utils/fade-easing';

interface FadeCurveProps {
	type: 'in' | 'out';
	width: number;
	height: number;
	backgroundColor: string;
}

const svgStyle: React.CSSProperties = {
	position: 'absolute',
	left: 0,
	top: 0,
	pointerEvents: 'none',
};

export const FadeCurveLine: React.FC<FadeCurveProps> = ({
	type,
	width,
	height,
	backgroundColor,
}) => {
	const numPoints = Math.round(width) + 1;

	const points = Array.from({length: numPoints}, (_, i) => {
		const x = (i / (numPoints - 1)) * width;

		const t = i / (numPoints - 1); // Normalized position 0..1

		// Always compute easing-in curve first, then invert for fade-out.
		const multiplier =
			type === 'in' ? fadeEasingFunction(t) : fadeEasingFunction(1 - t);

		// Map multiplier (0..1) to SVG y-coordinate (height..0)
		const y = height - multiplier * height;

		return {x, y};
	});

	const curvePath = points
		.map(({x, y}, idx) => `${idx === 0 ? 'M' : 'L'}${x},${y}`)
		.join(' ');

	const backgroundPath = `${curvePath} L${width},0 L0,0 Z`;

	return (
		<svg width={width} height={height} style={svgStyle}>
			<path d={backgroundPath} fill={backgroundColor} />
		</svg>
	);
};
