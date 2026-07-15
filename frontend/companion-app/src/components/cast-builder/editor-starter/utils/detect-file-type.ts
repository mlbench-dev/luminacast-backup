export type Dimensions = {width: number; height: number};

export type ImageFileType = {
	category: 'image';
	type: 'png' | 'jpeg' | 'gif' | 'webp' | 'bmp';
	dimensions: Dimensions | null;
};

export type VideoFileType = {
	category: 'video';
	type: 'iso-base-media' | 'webm' | 'riff' | 'transport-stream';
};

export type AudioFileType = {
	category: 'audio';
	type: 'wav' | 'mp3' | 'aac' | 'flac';
};

export type UnknownFileType = {
	category: 'unknown';
	type: 'unknown';
};

export type DetectedFileType =
	| ImageFileType
	| VideoFileType
	| AudioFileType
	| UnknownFileType;

const matches = (data: Uint8Array, pattern: number[], offset = 0): boolean => {
	return pattern.every((byte, i) => data[offset + i] === byte);
};

const getGifDimensions = (data: Uint8Array): Dimensions | null => {
	if (data.length < 10) return null;
	const view = new DataView(data.buffer, data.byteOffset);
	return {width: view.getUint16(6, true), height: view.getUint16(8, true)};
};

const getPngDimensions = (data: Uint8Array): Dimensions | null => {
	if (data.length < 24) return null;
	const view = new DataView(data.buffer, data.byteOffset);
	return {width: view.getUint32(16, false), height: view.getUint32(20, false)};
};

const getJpegDimensions = (data: Uint8Array): Dimensions | null => {
	let offset = 2;
	const readU16 = (o: number) => (data[o] << 8) | data[o + 1];
	while (offset < data.length - 1) {
		if (data[offset] !== 0xff) {
			offset++;
			continue;
		}
		const marker = data[offset + 1];
		// Skip padding bytes (0xFF followed by 0xFF)
		if (marker === 0xff) {
			offset++;
			continue;
		}
		// Skip restart markers (0xD0-0xD7) and standalone markers
		if (
			marker === 0x00 ||
			marker === 0x01 ||
			(marker >= 0xd0 && marker <= 0xd9)
		) {
			offset += 2;
			continue;
		}
		// SOF markers (baseline, progressive, etc.) contain dimensions
		// 0xC0=SOF0, 0xC1=SOF1, 0xC2=SOF2, 0xC3=SOF3, 0xC5-0xC7, 0xC9-0xCB, 0xCD-0xCF
		if (
			(marker >= 0xc0 && marker <= 0xc3) ||
			(marker >= 0xc5 && marker <= 0xc7) ||
			(marker >= 0xc9 && marker <= 0xcb) ||
			(marker >= 0xcd && marker <= 0xcf)
		) {
			if (offset + 9 > data.length) return null;
			const height = readU16(offset + 5);
			const width = readU16(offset + 7);
			// Skip thumbnail dimensions (typically small, e.g. 160x120)
			// These appear in EXIF APP1 segments before the main image SOF
			if (width > 256 && height > 256) {
				return {width, height};
			}
		}
		// Move to next segment
		if (offset + 4 > data.length) return null;
		const segmentLength = readU16(offset + 2);
		offset += 2 + segmentLength;
	}
	return null;
};

const getWebpDimensions = (data: Uint8Array): Dimensions | null => {
	if (data.length < 30) return null;
	if (data[12] === 0x56 && data[13] === 0x50 && data[14] === 0x38) {
		if (data[15] === 0x20) {
			return {
				width: data[26] | ((data[27] << 8) & 0x3fff),
				height: data[28] | ((data[29] << 8) & 0x3fff),
			};
		}
		if (data[15] === 0x4c) {
			return {
				width: 1 + (data[21] | ((data[22] & 0x3f) << 8)),
				height:
					1 +
					(((data[22] & 0xc0) >> 6) |
						(data[23] << 2) |
						((data[24] & 0x0f) << 10)),
			};
		}
		if (data[15] === 0x58) {
			return {
				width: 1 + (data[24] | (data[25] << 8) | (data[26] << 16)),
				height: 1 + (data[27] | (data[28] << 8) | (data[29] << 16)),
			};
		}
	}
	return null;
};

const getBmpDimensions = (data: Uint8Array): Dimensions | null => {
	if (data.length < 26) return null;
	const view = new DataView(data.buffer, data.byteOffset);
	return {
		width: view.getUint32(18, true),
		height: Math.abs(view.getInt32(22, true)),
	};
};

const detectFileTypeFromBytes = (data: Uint8Array): DetectedFileType => {
	// GIF: "GIF8"
	if (matches(data, [0x47, 0x49, 0x46, 0x38])) {
		return {category: 'image', type: 'gif', dimensions: getGifDimensions(data)};
	}
	// PNG: 0x89 "PNG"
	if (matches(data, [0x89, 0x50, 0x4e, 0x47])) {
		return {category: 'image', type: 'png', dimensions: getPngDimensions(data)};
	}
	// JPEG: 0xFF 0xD8
	if (matches(data, [0xff, 0xd8])) {
		return {
			category: 'image',
			type: 'jpeg',
			dimensions: getJpegDimensions(data),
		};
	}
	// BMP: "BM"
	if (matches(data, [0x42, 0x4d])) {
		return {category: 'image', type: 'bmp', dimensions: getBmpDimensions(data)};
	}
	// RIFF-based (WebP, WAV, AVI)
	if (matches(data, [0x52, 0x49, 0x46, 0x46])) {
		// Check file type at offset 8
		if (matches(data, [0x57, 0x45, 0x42, 0x50], 8)) {
			// "WEBP"
			return {
				category: 'image',
				type: 'webp',
				dimensions: getWebpDimensions(data),
			};
		}
		if (matches(data, [0x57, 0x41, 0x56, 0x45], 8)) {
			// "WAVE"
			return {category: 'audio', type: 'wav'};
		}
		if (matches(data, [0x41, 0x56, 0x49, 0x20], 8)) {
			// "AVI "
			return {category: 'video', type: 'riff'};
		}
	}
	// WebM/MKV: EBML header
	if (matches(data, [0x1a, 0x45, 0xdf, 0xa3])) {
		return {category: 'video', type: 'webm'};
	}
	// ISO base media (MP4, MOV, etc): "ftyp" at offset 4
	if (matches(data, [0x66, 0x74, 0x79, 0x70], 4)) {
		return {category: 'video', type: 'iso-base-media'};
	}
	// Transport Stream: sync byte 0x47 at 0 and 188
	if (data.length >= 189 && data[0] === 0x47 && data[188] === 0x47) {
		return {category: 'video', type: 'transport-stream'};
	}
	// MP3: ID3 tags or frame sync
	if (
		matches(data, [0xff, 0xf3]) ||
		matches(data, [0xff, 0xfb]) ||
		matches(data, [0x49, 0x44, 0x33]) // "ID3"
	) {
		return {category: 'audio', type: 'mp3'};
	}
	// AAC: ADTS sync
	if (matches(data, [0xff, 0xf1])) {
		return {category: 'audio', type: 'aac'};
	}
	// FLAC: "fLaC"
	if (matches(data, [0x66, 0x4c, 0x61, 0x43])) {
		return {category: 'audio', type: 'flac'};
	}

	return {category: 'unknown', type: 'unknown'};
};

// JPEG EXIF segments can contain embedded thumbnails with their own SOF markers.
// The main image SOF marker may appear after the EXIF data (often 10-20KB in).
const HEADER_BYTES = 32768;

export const detectFileType = async (file: Blob): Promise<DetectedFileType> => {
	const buffer = await file.slice(0, HEADER_BYTES).arrayBuffer();
	return detectFileTypeFromBytes(new Uint8Array(buffer));
};
