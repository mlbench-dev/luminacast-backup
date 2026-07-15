export const AlignCenterHorizontal: React.FC<React.SVGProps<SVGSVGElement>> = (
	props,
) => {
	return (
		<svg
			{...props}
			viewBox="0 0 16 16"
			fill="none"
			xmlns="http://www.w3.org/2000/svg"
		>
			<path
				d="M8 2V14"
				stroke="currentColor"
				strokeOpacity="0.5"
				strokeLinecap="square"
			/>
			<line
				x1="3"
				y1="6"
				x2="12"
				y2="6"
				stroke="currentColor"
				strokeWidth="2"
			/>
			<line
				x1="4"
				y1="10"
				x2="11"
				y2="10"
				stroke="currentColor"
				strokeWidth="2"
			/>
		</svg>
	);
};
