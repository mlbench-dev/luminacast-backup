import {createContext, useCallback, useMemo, useState} from 'react';

type EditMode = 'select' | 'draw-solid' | 'create-text';

type EditModeContextType = {
	editMode: EditMode;
	setEditMode: (editMode: EditMode) => void;
};

export const EditModeContext = createContext<EditModeContextType>({
	editMode: 'select',
	setEditMode: () => {},
});

export const EditModeProvider: React.FC<{
	children: React.ReactNode;
}> = ({children}) => {
	const [editModeState, setEditModeState] = useState<EditMode>('select');

	const setEditMode = useCallback((mode: EditMode) => {
		setEditModeState(mode);
	}, []);

	const contextValue = useMemo(
		() => ({
			editMode: editModeState,
			setEditMode,
		}),
		[editModeState, setEditMode],
	);

	return (
		<EditModeContext.Provider value={contextValue}>
			{children}
		</EditModeContext.Provider>
	);
};
