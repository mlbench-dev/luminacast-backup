import {createContext} from 'react';
import {Rect} from '../utils/fit-element-size-in-container';

export const CanvasSizeContext = createContext<Rect | null>(null);

export const CanvasSizeProvider = ({
	children,
	size,
}: {
	children: React.ReactNode;
	size: Rect | null;
}) => {
	return (
		<CanvasSizeContext.Provider value={size}>
			{children}
		</CanvasSizeContext.Provider>
	);
};
