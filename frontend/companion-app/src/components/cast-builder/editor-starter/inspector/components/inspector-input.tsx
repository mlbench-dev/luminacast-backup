import React, {forwardRef} from 'react';
import {clsx} from '../../utils/clsx';

const InspectorInputForwardRef: React.ForwardRefRenderFunction<
	HTMLInputElement,
	React.HTMLProps<HTMLInputElement> & {
		accessibilityLabel: string;
	}
> = ({className, accessibilityLabel, ...props}, ref) => {
	const onKeyDown = React.useCallback(
		(e: React.KeyboardEvent<HTMLInputElement>) => {
			e.stopPropagation();
		},
		[],
	);

	return (
		<input
			ref={ref}
			className={clsx(
				'editor-starter-field w-full px-2 py-2 text-xs text-neutral-300',
				className,
			)}
			type="text"
			onKeyDown={onKeyDown}
			{...props}
			aria-label={accessibilityLabel}
		></input>
	);
};

export const InspectorInput = forwardRef(InspectorInputForwardRef);
