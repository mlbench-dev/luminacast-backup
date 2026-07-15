export const TaskTitle: React.FC<{
	children: string;
}> = ({children}) => {
	return (
		<div
			className="w-full cursor-default overflow-hidden text-ellipsis whitespace-nowrap"
			title={children}
		>
			{children}
		</div>
	);
};
