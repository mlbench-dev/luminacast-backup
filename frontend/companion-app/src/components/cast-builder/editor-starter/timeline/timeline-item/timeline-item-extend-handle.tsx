import React, {ComponentProps} from 'react';

export const TimelineItemExtendHandle: React.FC<ComponentProps<'div'>> = ({
	className,
	...restProps
}) => {
	return (
		<div
			className={`group absolute top-0 bottom-0 flex items-center justify-center ${className ?? ''}`}
			{...restProps}
		></div>
	);
};
