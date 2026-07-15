import React, {HTMLProps} from 'react';
import {clsx} from '../../utils/clsx';

export const InspectorIconButton: React.FC<HTMLProps<HTMLButtonElement>> = ({
	className,
	...props
}) => {
	return (
		<button
			className={clsx(
				'editor-starter-focus-ring cursor-default rounded-sm p-1 hover:bg-white/5',
				className,
			)}
			{...props}
			type="button"
		></button>
	);
};
