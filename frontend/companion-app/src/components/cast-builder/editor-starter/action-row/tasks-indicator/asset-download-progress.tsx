import {AssetDownloadTask} from '../../caching/download-tasks';
import {DownloadIcon} from '../../icons/download-state';
import {formatBytes} from '../../utils/format-bytes';
import {IconContainer} from './icon-container';
import {TaskContainer} from './task-container';
import {TaskDescription} from './task-description';
import {TaskSubtitle} from './task-subtitle';
import {TaskTitle} from './task-title';

export const AssetDownloadProgress: React.FC<{
	assetDownloadTask: AssetDownloadTask;
}> = ({assetDownloadTask}) => {
	return (
		<TaskContainer>
			<IconContainer>
				<DownloadIcon />
			</IconContainer>
			<TaskDescription isError={false}>
				<TaskTitle>Downloading assets</TaskTitle>
				<TaskSubtitle>
					<div className="text-xs opacity-50">
						{formatBytes(assetDownloadTask.bytesRemaining)} remaining
					</div>
				</TaskSubtitle>
			</TaskDescription>
		</TaskContainer>
	);
};
