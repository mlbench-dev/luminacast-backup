export const AlignLeft: React.FC<React.SVGProps<SVGSVGElement>> = (props) => {
	return (
		<svg {...props} viewBox="0 0 16 16" fill="none">
			<path
				d="M2 2L2 14"
				stroke="currentcolor"
				strokeOpacity="0.5"
				strokeLinecap="square"
			/>
			<line
				x1="4"
				y1="6"
				x2="13"
				y2="6"
				stroke="currentcolor"
				strokeWidth="2"
			/>
			<line
				x1="4"
				y1="10"
				x2="11"
				y2="10"
				stroke="currentcolor"
				strokeWidth="2"
			/>
		</svg>
	);
};
