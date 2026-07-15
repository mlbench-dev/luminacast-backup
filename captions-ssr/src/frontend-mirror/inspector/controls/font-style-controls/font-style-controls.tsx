// SSR shim. Mirrors only turnFontStyleIntoCss, the single export
// caption-page.tsx imports from the frontend's font-style-controls. The
// real module also exports a React control that depends on the editor's
// state/context graph, which the headless caption render cannot load.
// Kept byte-for-byte identical to the frontend implementation.
import React from 'react';
import {FontStyle} from '../../../items/text/text-item-type';

export const turnFontStyleIntoCss = (
	fontStyle: FontStyle,
): React.CSSProperties => {
	return {
		...(fontStyle.variant.toLowerCase().includes('italic')
			? {fontStyle: 'italic'}
			: {}),
		fontWeight: fontStyle.weight,
	};
};
