export const TextIcon: React.FC<React.SVGProps<SVGSVGElement>> = (props) => {
	return (
		<svg
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth="2"
			{...props}
		>
			<path d="M4 7V4h16v3" />
			<path d="M9 20h6" />
			<path d="M12 4v16" />
		</svg>
	);
};
