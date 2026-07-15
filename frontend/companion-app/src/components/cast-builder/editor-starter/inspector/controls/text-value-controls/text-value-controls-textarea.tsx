import React, {forwardRef} from 'react';
import {clsx} from '../../../utils/clsx';

const TextValueControlsTextareaForwardRef: React.ForwardRefRenderFunction<
	HTMLTextAreaElement,
	React.HTMLProps<HTMLTextAreaElement>
> = ({className, ...props}, ref) => {
	const onKeyDown = React.useCallback(
		(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
			e.stopPropagation();
		},
		[],
	);

	return (
		<textarea
			ref={ref}
			className={clsx(
				className,
				'editor-starter-field field-sizing-content w-full px-2 py-2 text-xs text-neutral-300',
			)}
			onKeyDown={onKeyDown}
			{...props}
		/>
	);
};

export const TextValueControlsTextarea = forwardRef(
	TextValueControlsTextareaForwardRef,
);
