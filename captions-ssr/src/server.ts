import {bundle, WebpackOverrideFn} from '@remotion/bundler';
import {
	renderFrames,
	renderMedia,
	selectComposition,
} from '@remotion/renderer';
import express, {Request, Response} from 'express';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {CaptionToken} from './CaptionComposition';
import {CAPTION_COMPOSITION_ID, CaptionRenderProps} from './Root';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// The composition (and the mirrored caption-page) import the frontend's
// caption preset table via the `@/lib/captionPresets` alias. tsconfig `paths`
// only steer the type-checker; Remotion bundles with webpack, which resolves
// nothing unless we add the alias here. The frontend sources are copied into
// src/frontend-mirror at build time (Dockerfile) / by sync-frontend.sh, so
// `@` maps to that mirror — the single source of truth.
const FRONTEND_MIRROR = path.join(__dirname, 'frontend-mirror');
const webpackOverride: WebpackOverrideFn = (config) => ({
	...config,
	resolve: {
		...(config.resolve ?? {}),
		alias: {
			...(config.resolve?.alias ?? {}),
			'@/lib/captionPresets': path.join(FRONTEND_MIRROR, 'captionPresets.ts'),
			'@/lib': FRONTEND_MIRROR,
			'@': FRONTEND_MIRROR,
		},
	},
});

// ---- Config (all env-overridable; no hardcoded timeouts) ----
const PORT = Number(process.env.CAPTIONS_SSR_PORT || 3030);
const HOST = process.env.CAPTIONS_SSR_HOST || '0.0.0.0';
// Where rendered overlays land. The worker (Step 12) reads from here via a
// shared volume or fetches the streamed file.
const OUTPUT_DIR =
	process.env.CAPTIONS_SSR_OUTPUT_DIR || path.join(os.tmpdir(), 'captions_ssr');
// Output format: "png-sequence" (transparent PNGs) or "alpha-video"
// (VP8/VP9 webm with alpha). Default to PNG sequence — lossless alpha the
// composer can overlay frame-accurately.
const DEFAULT_FORMAT = (
	process.env.CAPTIONS_SSR_FORMAT || 'png-sequence'
).toLowerCase();
// Render concurrency (Remotion worker threads). Unset => Remotion default.
const RENDER_CONCURRENCY = process.env.CAPTIONS_SSR_CONCURRENCY
	? Number(process.env.CAPTIONS_SSR_CONCURRENCY)
	: null;
// Request body cap — token lists for long videos can be large.
const BODY_LIMIT = process.env.CAPTIONS_SSR_BODY_LIMIT || '32mb';

// Bundle once at boot and reuse the serve URL for every render.
let bundleUrlPromise: Promise<string> | null = null;
function getBundle(): Promise<string> {
	if (!bundleUrlPromise) {
		bundleUrlPromise = bundle({
			entryPoint: path.join(__dirname, 'index.ts'),
			webpackOverride,
		});
	}
	return bundleUrlPromise;
}

// Normalise the worker's token list to {text, startMs, endMs}. Accepts the
// FFmpeg burn path's _captions_tokens shape directly, plus the legacy
// {startInSeconds, endInSeconds} shape the composer still tolerates.
function normalizeTokens(raw: unknown): CaptionToken[] {
	if (!Array.isArray(raw)) {
		return [];
	}
	const out: CaptionToken[] = [];
	for (const t of raw) {
		if (!t || typeof t !== 'object') {
			continue;
		}
		const obj = t as Record<string, unknown>;
		const text = String(obj.text ?? '').trim();
		if (!text) {
			continue;
		}
		let startMs: number;
		let endMs: number;
		if (obj.startMs !== undefined && obj.startMs !== null) {
			startMs = Math.round(Number(obj.startMs) || 0);
			endMs = Math.round(Number(obj.endMs ?? obj.startMs) || startMs);
		} else {
			startMs = Math.round((Number(obj.startInSeconds) || 0) * 1000);
			endMs = Math.round((Number(obj.endInSeconds) || 0) * 1000);
		}
		if (endMs <= startMs) {
			endMs = startMs + 1;
		}
		out.push({text, startMs, endMs});
	}
	return out;
}

function buildInputProps(body: Record<string, unknown>): CaptionRenderProps {
	const canvas = (body.canvas as Record<string, unknown>) || {};
	const block = (body.block as Record<string, unknown>) || {};
	const width = Math.max(1, Math.round(Number(canvas.width) || 1080));
	const height = Math.max(1, Math.round(Number(canvas.height) || 1920));
	const fps = Math.max(1, Math.round(Number(canvas.fps) || 30));
	const pageMs = Math.max(
		200,
		Math.round(Number(block.pageDurationInMilliseconds) || 1200),
	);
	const switchMs = Math.max(
		200,
		Math.round(
			Number(block.switchCaptionsEveryMs) || pageMs,
		),
	);
	return {
		tokens: normalizeTokens(body.tokens),
		presetId: String(body.presetId || 'hormozi_bold'),
		pageDurationInMilliseconds: pageMs,
		switchCaptionsEveryMs: switchMs,
		fontFamily: String(block.fontFamily || ''),
		fontStyleVariant: String(block.fontStyleVariant || 'normal'),
		fontStyleWeight: String(block.fontStyleWeight || ''),
		lineHeight: Number(block.lineHeight) || 1.2,
		letterSpacing: Number(block.letterSpacing) || 0,
		// Single caption line by default — a longer caption is re-split into
		// one-line pages timed to the words. Explicit block.maxLines wins.
		maxLines: Math.max(1, Math.round(Number(block.maxLines) || 1)),
		captionWidth: Math.max(
			1,
			Math.round(Number(block.captionWidth) || width * 0.9),
		),
		// regr-2c: bottom safe-area margin (percent). Clamp to a sane band so a
		// bad value can't hide the captions. Defaults to 18% when unset.
		safeBottomPct: Math.max(
			0,
			Math.min(45, Number(block.safeBottomPct) || 18),
		),
		// regr-2c: hard per-chunk character budget. 0 => disabled (time windows
		// only). Sent by the orchestrator from font size / width / maxLines.
		maxCharsPerChunk: Math.max(0, Math.round(Number(block.maxCharsPerChunk) || 0)),
		width,
		height,
		fps,
	};
}

const app = express();
app.use(express.json({limit: BODY_LIMIT}));

app.get('/health', (_req: Request, res: Response) => {
	res.json({status: 'ok'});
});

// POST /render
// Body: {
//   tokens: [{text, startMs, endMs}, ...],   // WhisperX word timings
//   presetId: "hormozi_bold",
//   canvas: {width, height, fps},
//   block:  {pageDurationInMilliseconds, switchCaptionsEveryMs, fontFamily,
//            fontStyleVariant, fontStyleWeight, lineHeight, letterSpacing,
//            maxLines, captionWidth},
//   format: "png-sequence" | "alpha-video"   // optional, defaults by env
// }
// Returns: { outputPath, format, width, height, fps, durationInFrames }
app.post('/render', async (req: Request, res: Response) => {
	const started = Date.now();
	try {
		const body = (req.body as Record<string, unknown>) || {};
		const inputProps = buildInputProps(body);

		if (inputProps.tokens.length === 0) {
			res.status(400).json({error: 'no caption tokens provided'});
			return;
		}

		const format = String(body.format || DEFAULT_FORMAT).toLowerCase();
		const jobId = randomUUID();
		const jobDir = path.join(OUTPUT_DIR, jobId);
		fs.mkdirSync(jobDir, {recursive: true});

		const serveUrl = await getBundle();
		const composition = await selectComposition({
			serveUrl,
			id: CAPTION_COMPOSITION_ID,
			inputProps,
		});

		let outputPath: string;

		if (format === 'alpha-video') {
			// VP8 webm carries an alpha channel the composer can overlay.
			outputPath = path.join(jobDir, 'captions.webm');
			await renderMedia({
				composition,
				serveUrl,
				codec: 'vp8',
				pixelFormat: 'yuva420p',
				imageFormat: 'png',
				outputLocation: outputPath,
				inputProps,
				...(RENDER_CONCURRENCY ? {concurrency: RENDER_CONCURRENCY} : {}),
			});
		} else {
			// Transparent PNG sequence: captions-%04d.png in the job dir.
			outputPath = jobDir;
			await renderFrames({
				composition,
				serveUrl,
				imageFormat: 'png',
				outputDir: jobDir,
				frameRange: null,
				inputProps,
				onFrameUpdate: () => undefined,
				onStart: () => undefined,
				...(RENDER_CONCURRENCY ? {concurrency: RENDER_CONCURRENCY} : {}),
			});
		}

		res.json({
			outputPath,
			format: format === 'alpha-video' ? 'alpha-video' : 'png-sequence',
			width: composition.width,
			height: composition.height,
			fps: composition.fps,
			durationInFrames: composition.durationInFrames,
			elapsedMs: Date.now() - started,
		});
	} catch (e) {
		// Log full detail server-side; keep the client message engine-agnostic.
		console.error('[captions-ssr] render failed', e);
		res.status(500).json({error: 'caption overlay render failed'});
	}
});

app.listen(PORT, HOST, () => {
	console.log(`[captions-ssr] listening on http://${HOST}:${PORT}`);
	// Warm the bundle so the first /render isn't slowed by webpack startup.
	getBundle().catch((e) => console.error('[captions-ssr] bundle warm failed', e));
});
