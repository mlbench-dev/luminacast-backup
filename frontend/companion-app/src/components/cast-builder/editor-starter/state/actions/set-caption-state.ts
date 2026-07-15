import {
	CaptioningTask,
	CaptioningTaskStatus,
} from '../../captioning/caption-state';
import {EditorState} from '../types';

export const addCaptioningTask = ({
	state,
	newTask,
}: {
	state: EditorState;
	newTask: CaptioningTask;
}): EditorState => {
	return {
		...state,
		captioningTasks: [newTask, ...state.captioningTasks],
	};
};

export const updateCaptioningTask = ({
	state,
	taskId,
	newStatus,
}: {
	state: EditorState;
	taskId: string;
	newStatus: CaptioningTaskStatus;
}): EditorState => {
	let changed = false;

	const tasks = state.captioningTasks.map((task) => {
		if (task.id === taskId) {
			changed = true;
			return {...task, status: newStatus};
		}

		return task;
	});

	if (!changed) {
		return state;
	}

	return {
		...state,
		captioningTasks: tasks,
	};
};

export const deleteCaptioningTask = ({
	state,
	taskId,
}: {
	state: EditorState;
	taskId: string;
}): EditorState => {
	const newTasks = state.captioningTasks.filter((task) => task.id !== taskId);
	if (newTasks.length === state.captioningTasks.length) {
		return state;
	}

	return {
		...state,
		captioningTasks: newTasks,
	};
};

export const canClearCaptioningTask = (task: CaptioningTask) => {
	return task.status.type === 'done' || task.status.type === 'error';
};

export const clearFinalizedCaptioningTasks = (
	state: EditorState,
): EditorState => {
	const newTasks = state.captioningTasks.filter(
		(task) => !canClearCaptioningTask(task),
	);

	if (newTasks.length === state.captioningTasks.length) {
		return state;
	}

	return {
		...state,
		captioningTasks: [],
	};
};
