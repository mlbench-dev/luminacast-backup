export type VideoCodec = 'h264' | 'vp8';

export const getEditorExportFileName = (codec: VideoCodec) => {
	return `editor-export.${codec === 'h264' ? 'mp4' : 'webm'}`;
};
