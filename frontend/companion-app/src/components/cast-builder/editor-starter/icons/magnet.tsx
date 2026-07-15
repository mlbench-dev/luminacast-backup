import {SVGProps} from 'react';

export const MagnetIcon: React.FC<SVGProps<SVGSVGElement>> = (props) => {
	return (
		<svg
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 512 512"
			fill="currentColor"
			{...props}
		>
			{/* Font Awesome Free v7.0.0 by @fontawesome - https://fontawesome.com */}
			<path d="M32 176L32 288C32 411.7 132.3 512 256 512S480 411.7 480 288l0-112-128 0 0 112c0 53-43 96-96 96s-96-43-96-96l0-112-128 0zm0-48l128 0 0-64c0-17.7-14.3-32-32-32L64 32C46.3 32 32 46.3 32 64l0 64zm320 0l128 0 0-64c0-17.7-14.3-32-32-32l-64 0c-17.7 0-32 14.3-32 32l0 64z" />
		</svg>
	);
};
