export type PresignResponse = {
	readUrl: string;
	presignedUrl: string;
	fileKey: string;
};

export type PresignErrorCode = 'INTERNAL_ERROR' | 'UNKNOWN_ERROR' | '404_ERROR';

export interface PresignErrorResponse {
	code: PresignErrorCode;
	message: string;
}
