export const IconContainer: React.FC<{
	children: React.ReactNode;
}> = ({children}) => {
	return (
		<div className="flex h-5 w-5 items-center justify-center">{children}</div>
	);
};
