export const HorizontalPaddingIcon: React.FC<React.SVGProps<SVGSVGElement>> = (
	props,
) => {
	return (
		<svg
			{...props}
			viewBox="0 0 16 16"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
		>
			<rect
				x="5"
				y="4"
				width="6"
				height="8"
				stroke="currentColor"
				fill="none"
			/>
			<line
				x1="2"
				y1="4"
				x2="2"
				y2="12"
				stroke="currentColor"
				strokeWidth="1.5"
				strokeLinecap="round"
			/>
			<line
				x1="14"
				y1="4"
				x2="14"
				y2="12"
				stroke="currentColor"
				strokeWidth="1.5"
				strokeLinecap="round"
			/>
		</svg>
	);
};
