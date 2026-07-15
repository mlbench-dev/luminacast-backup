import React from 'react';
import {AlignCenterHorizontal} from './align-center-horizontal';

export const AlignCenterVertical: React.FC<React.SVGProps<SVGSVGElement>> = (
	props,
) => {
	return (
		<AlignCenterHorizontal
			{...props}
			style={{
				transform: 'rotate(90deg)',
			}}
		></AlignCenterHorizontal>
	);
};
