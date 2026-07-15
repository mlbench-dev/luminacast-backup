import {useCallback, useMemo} from 'react';

type KeyEventType = 'keydown' | 'keyup' | 'keypress';

type KeyListenerCallback = (e: KeyboardEvent) => void;

type RegisteredKeybinding = {
	id: string;
	key: string;
	event: KeyEventType;
	callback: KeyListenerCallback;
};

let registered: RegisteredKeybinding[] = [];

const regKeybinding = (binding: RegisteredKeybinding) => {
	registered = [...registered, binding];
	window.addEventListener(binding.event, binding.callback);
};

const unregisterKeybinding = (binding: RegisteredKeybinding) => {
	registered = registered.filter((r) => {
		if (r.id === binding.id) {
			window.removeEventListener(binding.event, binding.callback);

			return false;
		}

		return true;
	});
};

export const useKeybinding = () => {
	const registerKeybinding = useCallback(
		(options: {
			event: KeyEventType;
			key: string;
			commandCtrlKey: boolean;
			callback: (e: KeyboardEvent) => void;
			preventDefault: boolean;
			triggerIfInputFieldFocused: boolean;
		}) => {
			const listener = (e: KeyboardEvent) => {
				const commandKey = window.navigator.platform.startsWith('Mac')
					? e.metaKey
					: e.ctrlKey;
				if (
					e.key.toLowerCase() === options.key.toLowerCase() &&
					options.commandCtrlKey === commandKey
				) {
					if (!options.triggerIfInputFieldFocused) {
						const {activeElement} = document;
						if (activeElement instanceof HTMLInputElement) {
							return;
						}

						if (activeElement instanceof HTMLTextAreaElement) {
							return;
						}
					}

					options.callback(e);
					if (options.preventDefault) {
						e.preventDefault();
					}
				}
			};

			const toRegister: RegisteredKeybinding = {
				event: options.event,
				key: options.key,
				callback: listener,
				id: String(Math.random()),
			};

			regKeybinding(toRegister);
			return {
				unregister: () => unregisterKeybinding(toRegister),
			};
		},
		[],
	);

	return useMemo(() => ({registerKeybinding}), [registerKeybinding]);
};
