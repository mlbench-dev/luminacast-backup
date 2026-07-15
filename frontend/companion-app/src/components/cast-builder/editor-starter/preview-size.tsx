import {createContext} from 'react';

type Translation = {
	x: number;
	y: number;
};

export type PreviewSize = {
	size: number | 'auto';
	translation: Translation;
};

export type PreviewSizeCtx = {
	size: PreviewSize;
	setSize: (cb: (oldSize: PreviewSize) => PreviewSize) => void;
};

export const PreviewSizeContext = createContext<PreviewSizeCtx>({
	setSize: () => {
		throw new Error('setSize not implemented');
	},
	size: {size: 'auto', translation: {x: 0, y: 0}},
});
