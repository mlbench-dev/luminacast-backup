import React, {HTMLProps} from 'react';
import {clsx} from './utils/clsx';

export const IconButton: React.FC<HTMLProps<HTMLButtonElement>> = ({
	className,
	children,
	...props
}) => {
	return (
		<button
			className={clsx(
				`editor-starter-focus-ring flex items-center gap-1 rounded-sm p-1 hover:bg-white/5`,
				className,
			)}
			{...props}
			type="button"
		>
			{children}
		</button>
	);
};
