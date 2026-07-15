export function TimelineItemPreviewContainer({
	children,
	style,
	isSelected,
}: {
	children: React.ReactNode;
	style: React.CSSProperties;
	isSelected: boolean;
}) {
	return (
		<div
			className={`pointer-events-none absolute box-border rounded-sm border border-black select-none ${isSelected ? 'border-editor-starter-accent' : ''}`}
			style={{...style, overflow: 'hidden'}}
		>
			{children}
		</div>
	);
}
