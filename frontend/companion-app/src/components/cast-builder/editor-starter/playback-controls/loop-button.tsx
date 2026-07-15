import React, {useCallback} from 'react';
import {LoopIcon} from '../icons/loop';
import {setLoop} from '../state/actions/loop';
import {saveLoop} from '../state/loop-persistance';
import {useLoop, useWriteContext} from '../utils/use-context';

export const LoopButton: React.FC = () => {
	const {setState} = useWriteContext();

	const loop = useLoop();
	const onClickLoop = useCallback(() => {
		setState({
			update: (state) => {
				saveLoop(!state.loop);

				return setLoop(state, !state.loop);
			},
			commitToUndoStack: false,
		});
	}, [setState]);

	return (
		<button
			className="editor-starter-focus-ring p-2"
			type="button"
			onClick={onClickLoop}
			title="Loop"
			aria-label="Loop"
		>
			<LoopIcon
				className={loop ? 'fill-editor-starter-accent' : 'text-neutral-300'}
			/>
		</button>
	);
};
