import {createContext, useContext} from 'react';
import {CanvasTransformation} from './calculate-canvas-transformation';

export const CanvasTransformationContext = createContext<CanvasTransformation>({
	centerX: 0,
	centerY: 0,
	xCorrection: 0,
	yCorrection: 0,
	scale: 1,
});

export const useCanvasTransformationScale = () => {
	const context = useContext(CanvasTransformationContext);

	if (!context) {
		throw new Error(
			'useScale must be used within a CanvasTransformationContext',
		);
	}

	return context.scale;
};
