import {AssetState, EditorStarterAsset} from '../../../assets/assets';
import {useDownloadProgress} from '../../../caching/load-to-blob-url';
import {formatBytes} from '../../../utils/format-bytes';

const iconStyle: React.CSSProperties = {
	height: 14,
};

const InfoUploading: React.FC<{
	status: AssetState;
}> = ({status}) => {
	if (status.type !== 'in-progress') {
		throw new Error('Asset status is not in-progress');
	}

	const remainingBytes =
		status.progress.totalBytes - status.progress.loadedBytes;

	return (
		<>
			<svg style={iconStyle} viewBox="0 0 640 640">
				<path
					fill="currentcolor"
					d="M342.6 81.4C330.1 68.9 309.8 68.9 297.3 81.4L137.3 241.4C124.8 253.9 124.8 274.2 137.3 286.7C149.8 299.2 170.1 299.2 182.6 286.7L288 181.3L288 552C288 569.7 302.3 584 320 584C337.7 584 352 569.7 352 552L352 181.3L457.4 286.7C469.9 299.2 490.2 299.2 502.7 286.7C515.2 274.2 515.2 253.9 502.7 241.4L342.7 81.4z"
				/>
			</svg>
			<div className="leading-4">
				{remainingBytes === 0 ? 'Processing...' : formatBytes(remainingBytes)}
			</div>
		</>
	);
};

const InfoDownloading: React.FC<{
	asset: EditorStarterAsset;
	downloadedBytes: number;
}> = ({asset, downloadedBytes}) => {
	const remainingBytes = asset.size - downloadedBytes;

	return (
		<>
			<svg style={iconStyle} viewBox="0 0 640 640">
				<path
					fill="currentcolor"
					d="M297.4 566.6C309.9 579.1 330.2 579.1 342.7 566.6L502.7 406.6C515.2 394.1 515.2 373.8 502.7 361.3C490.2 348.8 469.9 348.8 457.4 361.3L352 466.7L352 96C352 78.3 337.7 64 320 64C302.3 64 288 78.3 288 96L288 466.7L182.6 361.3C170.1 348.8 149.8 348.8 137.3 361.3C124.8 373.8 124.8 394.1 137.3 406.6L297.3 566.6z"
				/>
			</svg>
			<div className="leading-4">{formatBytes(remainingBytes)}</div>
		</>
	);
};

const InfoUploaded: React.FC<{
	asset: EditorStarterAsset;
}> = ({asset}) => {
	return (
		<>
			<svg style={iconStyle} viewBox="0 0 640 640">
				<path
					fill="currentcolor"
					d="M32 400C32 479.5 96.5 544 176 544L480 544C550.7 544 608 486.7 608 416C608 364.4 577.5 319.9 533.5 299.7C540.2 286.6 544 271.7 544 256C544 203 501 160 448 160C430.3 160 413.8 164.8 399.6 173.1C375.5 127.3 327.4 96 272 96C192.5 96 128 160.5 128 240C128 248 128.7 255.9 129.9 263.5C73 282.7 32 336.6 32 400z"
				/>
			</svg>
			<div className="leading-4">{formatBytes(asset.size)}</div>
		</>
	);
};

export const UploadInfo: React.FC<{
	asset: EditorStarterAsset;
	status: AssetState;
}> = ({asset, status}) => {
	const downloadInfo = useDownloadProgress(asset.id) ?? null;

	return (
		<div className="flex flex-row items-center gap-[6px]">
			{downloadInfo !== null && downloadInfo < asset.size ? (
				<InfoDownloading asset={asset} downloadedBytes={downloadInfo} />
			) : status.type === 'uploaded' ? (
				<InfoUploaded asset={asset} />
			) : status.type === 'in-progress' ? (
				<InfoUploading status={status} />
			) : null}
		</div>
	);
};
