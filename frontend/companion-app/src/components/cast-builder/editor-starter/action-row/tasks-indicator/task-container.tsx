export const TaskContainer: React.FC<React.HTMLAttributes<HTMLDivElement>> = ({
	children,
	...divProps
}) => {
	return (
		<div
			className="flex w-full flex-row items-center gap-3 px-3 py-2 text-sm hover:bg-white/5"
			{...divProps}
		>
			{children}
		</div>
	);
};
