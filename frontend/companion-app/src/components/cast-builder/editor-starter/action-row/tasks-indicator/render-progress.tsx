import {useCallback, useMemo, useRef, useState} from 'react';
import {IconButton} from '../../icon-button';
import {CheckIcon} from '../../icons/check';
import {DownloadIcon} from '../../icons/download-state';
import {FileIcon} from '../../icons/file';
import {RenderProgressBottomLine} from '../../rendering/render-progress-bottom-line';
import {RenderingTask} from '../../rendering/render-state';
import {deleteRenderingTask} from '../../state/actions/set-render-state';
import {useWriteContext} from '../../utils/use-context';
import {IconContainer} from './icon-container';
import {TaskContainer} from './task-container';
import {TaskDescription} from './task-description';
import {TaskSubtitle} from './task-subtitle';
import {TaskTitle} from './task-title';
import {taskIndicatorRef} from './tasks-indicator';

export const RenderProgress: React.FC<{
	renderTask: RenderingTask;
}> = ({renderTask}) => {
	const a = useRef<HTMLAnchorElement>(null);
	const {setState} = useWriteContext();

	const onClick = useCallback(() => {
		a.current?.click();
		taskIndicatorRef.current?.close();
	}, [a]);

	const title = useMemo(() => {
		return `${renderTask.codec === 'h264' ? 'MP4' : 'WebM'} export`;
	}, [renderTask.codec]);

	const [hovered, setHovered] = useState(false);

	const onMouseEnter = useCallback(() => {
		setHovered(true);
	}, []);

	const onMouseLeave = useCallback(() => {
		setHovered(false);
	}, []);

	const onDismiss = useCallback(() => {
		setState({
			commitToUndoStack: false,
			update: (prevState) => {
				return deleteRenderingTask({
					state: prevState,
					taskId: renderTask.id,
				});
			},
		});
	}, [renderTask.id, setState]);

	return (
		<TaskContainer onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
			<IconContainer>
				<FileIcon />
			</IconContainer>
			<TaskDescription isError={renderTask.status.type === 'error'}>
				<TaskTitle>{title}</TaskTitle>
				<TaskSubtitle>
					<RenderProgressBottomLine renderTask={renderTask} />
				</TaskSubtitle>
			</TaskDescription>
			<div className="flex">
				{hovered &&
				(renderTask.status.type === 'error' ||
					renderTask.status.type === 'done') ? (
					<>
						<IconButton onClick={onDismiss} aria-label="Dismiss">
							<CheckIcon />
						</IconButton>
					</>
				) : null}
				{renderTask.status.type === 'done' ? (
					<>
						<a
							className="hidden"
							href={renderTask.status.outputFile}
							target="_blank"
							ref={a}
						/>
						<IconButton onClick={onClick} aria-label="Download">
							<DownloadIcon />
						</IconButton>
					</>
				) : null}
			</div>
		</TaskContainer>
	);
};
