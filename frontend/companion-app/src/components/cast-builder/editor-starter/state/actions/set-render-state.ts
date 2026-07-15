import {RenderingTask, RenderingTaskState} from '../../rendering/render-state';
import {EditorState} from '../types';

export const addRenderingTask = ({
	state,
	task,
}: {
	state: EditorState;
	task: RenderingTask;
}): EditorState => {
	return {
		...state,
		renderingTasks: [task, ...state.renderingTasks],
	};
};

export const updateRenderingTask = ({
	state,
	taskId,
	newStatus,
}: {
	state: EditorState;
	taskId: string;
	newStatus: RenderingTaskState;
}): EditorState => {
	const tasks = state.renderingTasks.map((task) => {
		if (task.id === taskId) {
			return {...task, status: newStatus};
		}
		return task;
	});

	return {
		...state,
		renderingTasks: tasks,
	};
};

export const deleteRenderingTask = ({
	state,
	taskId,
}: {
	state: EditorState;
	taskId: string;
}): EditorState => {
	const newTasks = state.renderingTasks.filter((task) => task.id !== taskId);
	if (newTasks.length === state.renderingTasks.length) {
		return state;
	}

	return {
		...state,
		renderingTasks: newTasks,
	};
};

export const canClearRenderingTask = (task: RenderingTask) => {
	return task.status.type === 'done' || task.status.type === 'error';
};

export const clearFinalizedRenderingTasks = (
	state: EditorState,
): EditorState => {
	const newTasks = state.renderingTasks.filter(
		(task) => !canClearRenderingTask(task),
	);

	if (newTasks.length === state.renderingTasks.length) {
		return state;
	}

	return {
		...state,
		renderingTasks: newTasks,
	};
};
