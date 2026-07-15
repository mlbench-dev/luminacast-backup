import React, {useMemo} from 'react';
import {
	CANVAS_SNAP_INDICATOR_COLOR,
	CANVAS_SNAP_LINE_WIDTH,
} from '../../constants';
import {useCanvasTransformationScale} from '../../utils/canvas-transformation-context';
import {useDimensions} from '../../utils/use-context';
import {CanvasSnapPoint} from './canvas-snap-types';

interface CanvasSnapIndicatorsProps {
	activeSnapPoints: CanvasSnapPoint[];
}

/**
 * Renders visual guide lines for active canvas snap points.
 * Lines span the full composition dimension.
 */
const CanvasSnapIndicatorsUnmemoized: React.FC<CanvasSnapIndicatorsProps> = ({
	activeSnapPoints,
}) => {
	const {compositionWidth, compositionHeight} = useDimensions();
	const scale = useCanvasTransformationScale();

	const lines = useMemo(() => {
		return activeSnapPoints.map((snapPoint, index) => {
			const {target} = snapPoint;

			// Horizontal orientation means vertical line (snaps horizontal positions)
			// Vertical orientation means horizontal line (snaps vertical positions)
			if (target.orientation === 'horizontal') {
				const width = Math.ceil(CANVAS_SNAP_LINE_WIDTH / scale);
				return (
					<div
						key={`h-${index}`}
						style={{
							position: 'absolute',
							left: target.position,
							top: 0,
							width,
							height: compositionHeight,
							backgroundColor: CANVAS_SNAP_INDICATOR_COLOR,
							pointerEvents: 'none',
						}}
					/>
				);
			} else {
				const height = Math.ceil(CANVAS_SNAP_LINE_WIDTH / scale);
				return (
					<div
						key={`v-${index}`}
						style={{
							position: 'absolute',
							left: 0,
							top: target.position,
							width: compositionWidth,
							height,
							backgroundColor: CANVAS_SNAP_INDICATOR_COLOR,
							pointerEvents: 'none',
						}}
					/>
				);
			}
		});
	}, [activeSnapPoints, compositionWidth, compositionHeight, scale]);

	if (activeSnapPoints.length === 0) {
		return null;
	}

	return <>{lines}</>;
};

export const CanvasSnapIndicators = React.memo(CanvasSnapIndicatorsUnmemoized);
