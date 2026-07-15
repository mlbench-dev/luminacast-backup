import {AssetUploadTask} from '../../assets/assets';
import {UploadIcon} from '../../icons/upload';
import {formatBytes} from '../../utils/format-bytes';
import {IconContainer} from './icon-container';
import {TaskContainer} from './task-container';
import {TaskDescription} from './task-description';
import {TaskSubtitle} from './task-subtitle';
import {TaskTitle} from './task-title';

export const AssetUploadProgress: React.FC<{
	assetUploadTask: AssetUploadTask;
}> = ({assetUploadTask}) => {
	return (
		<TaskContainer>
			<IconContainer>
				<UploadIcon />
			</IconContainer>
			<TaskDescription isError={false}>
				<TaskTitle>{assetUploadTask.asset.filename ?? 'File'}</TaskTitle>
				<TaskSubtitle>
					<div className="text-xs opacity-50">
						{formatBytes(assetUploadTask.status.loadedBytes)} /{' '}
						{formatBytes(assetUploadTask.status.totalBytes)}
					</div>
				</TaskSubtitle>
			</TaskDescription>
		</TaskContainer>
	);
};
