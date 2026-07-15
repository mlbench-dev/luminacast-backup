import {AssetUploadProgress} from '../assets/assets';

type OnUploadProgress = (options: AssetUploadProgress) => void;

export const uploadWithProgress = ({
	file,
	url,
	onProgress,
}: {
	file: Blob;
	url: string;
	onProgress: OnUploadProgress;
}) => {
	const xhr = new XMLHttpRequest();

	xhr.open('PUT', url);

	xhr.upload.onprogress = function (event) {
		if (event.lengthComputable) {
			onProgress({
				progress: event.loaded / event.total,
				loadedBytes: event.loaded,
				totalBytes: event.total,
			});
		}
	};

	const {resolve, reject, promise} = Promise.withResolvers<void>();

	xhr.onload = function () {
		if (xhr.status === 200) {
			resolve();
		} else {
			reject(new Error(`Upload failed with status: ${xhr.status}`));
		}
	};

	xhr.onerror = function () {
		reject(new Error('Failed to upload'));
	};

	xhr.setRequestHeader('Content-Type', file.type);
	xhr.setRequestHeader('Cache-Control', 'public, max-age=31536000');
	xhr.send(file);

	return promise;
};
