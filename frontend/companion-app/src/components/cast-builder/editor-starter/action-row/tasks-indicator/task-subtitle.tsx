export const TaskSubtitle: React.FC<{
	children: React.ReactNode;
}> = ({children}) => {
	return (
		<div className="relative flex min-h-4 cursor-default items-center">
			{children}
		</div>
	);
};
