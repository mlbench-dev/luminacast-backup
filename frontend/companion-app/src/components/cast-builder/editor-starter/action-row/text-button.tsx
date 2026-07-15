import React from 'react';
import {clsx} from '../utils/clsx';

export const TextButton: React.FC<React.HTMLProps<HTMLButtonElement>> = ({
	className,
	...props
}) => {
	return (
		<button
			className={clsx(
				className,
				'editor-starter-focus-ring bg-editor-starter-bg w-full rounded-sm px-2 py-2 text-xs text-neutral-300 enabled:cursor-pointer enabled:hover:bg-white/5 data-[disabled=true]:opacity-50',
			)}
			{...props}
			data-disabled={props.disabled}
			type="button"
		/>
	);
};
