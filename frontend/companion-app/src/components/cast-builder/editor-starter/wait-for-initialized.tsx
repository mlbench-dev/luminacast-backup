import {useContext} from 'react';
import {StateInitializedContext} from './context-provider';

export const WaitForInitialized = ({children}: {children: React.ReactNode}) => {
	const initialized = useContext(StateInitializedContext);
	if (!initialized) {
		return null;
	}

	return <>{children}</>;
};
