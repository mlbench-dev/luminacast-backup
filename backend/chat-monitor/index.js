/**
 * Luminacast Chat Monitor
 * Bridges TikTok Live chat to the Orchestrator via WebSocket.
 * Uses tiktok-live-connector to receive real-time chat events.
 */
require('dotenv').config();
const { WebcastPushConnection } = require('tiktok-live-connector');
const WebSocket = require('ws');

const ORCHESTRATOR_WS_URL = process.env.ORCHESTRATOR_WS_URL || 'ws://orchestrator:8000/ws/internal/chat';
const INTERNAL_SERVICE_SECRET = process.env.INTERNAL_SERVICE_SECRET || '';

let orchestratorWs = null;
let reconnectTimer = null;
const activeSessions = new Map(); // sessionId -> TikTokConnection

/**
 * Connect to orchestrator WebSocket with authentication.
 */
function connectToOrchestrator() {
    const url = `${ORCHESTRATOR_WS_URL}?token=${INTERNAL_SERVICE_SECRET}`;

    console.log(`[ChatMonitor] Connecting to orchestrator: ${ORCHESTRATOR_WS_URL}`);

    orchestratorWs = new WebSocket(url);

    orchestratorWs.on('open', () => {
        console.log('[ChatMonitor] Connected to orchestrator');
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    });

    orchestratorWs.on('message', (data) => {
        try {
            const msg = JSON.parse(data.toString());
            handleOrchestratorMessage(msg);
        } catch (e) {
            console.error('[ChatMonitor] Failed to parse orchestrator message:', e.message);
        }
    });

    orchestratorWs.on('close', (code, reason) => {
        console.log(`[ChatMonitor] Disconnected from orchestrator: ${code} ${reason}`);
        scheduleReconnect();
    });

    orchestratorWs.on('error', (err) => {
        console.error('[ChatMonitor] WebSocket error:', err.message);
    });
}

/**
 * Reconnect with exponential backoff.
 */
function scheduleReconnect() {
    if (reconnectTimer) return;
    const delay = 5000; // 5 seconds
    console.log(`[ChatMonitor] Reconnecting in ${delay / 1000}s...`);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectToOrchestrator();
    }, delay);
}

/**
 * Send message to orchestrator.
 */
function sendToOrchestrator(type, payload) {
    if (orchestratorWs && orchestratorWs.readyState === WebSocket.OPEN) {
        orchestratorWs.send(JSON.stringify({ type, payload }));
    }
}

/**
 * Handle messages from orchestrator (e.g., start/stop monitoring).
 */
function handleOrchestratorMessage(msg) {
    const { type, payload } = msg;

    switch (type) {
        case 'START_MONITORING':
            startTikTokMonitoring(payload.session_id, payload.tiktok_username);
            break;
        case 'STOP_MONITORING':
            stopTikTokMonitoring(payload.session_id);
            break;
        case 'SEND_CHAT':
            // TikTok chat sending (if supported)
            console.log(`[ChatMonitor] Send chat request: ${payload.text}`);
            break;
        default:
            console.log(`[ChatMonitor] Unknown message type: ${type}`);
    }
}

/**
 * Start monitoring a TikTok live stream.
 */
function startTikTokMonitoring(sessionId, tiktokUsername) {
    if (activeSessions.has(sessionId)) {
        console.log(`[ChatMonitor] Already monitoring session ${sessionId}`);
        return;
    }

    console.log(`[ChatMonitor] Starting TikTok monitoring: @${tiktokUsername} (session: ${sessionId})`);

    const tiktokConnection = new WebcastPushConnection(tiktokUsername, {
        sessionId: process.env.TIKTOK_SESSION_ID || undefined,
    });

    tiktokConnection.connect().then((state) => {
        console.log(`[ChatMonitor] Connected to TikTok live: ${state.roomId}`);
        activeSessions.set(sessionId, tiktokConnection);
    }).catch((err) => {
        console.error(`[ChatMonitor] Failed to connect to TikTok: ${err.message}`);
        sendToOrchestrator('MONITORING_ERROR', {
            session_id: sessionId,
            error: err.message,
        });
    });

    // Chat messages
    tiktokConnection.on('chat', (data) => {
        sendToOrchestrator('INCOMING_CHAT', {
            session_id: sessionId,
            username: data.uniqueId,
            message: data.comment,
            user_id: data.userId,
            timestamp: new Date().toISOString(),
        });
    });

    // Gift/Purchase events
    tiktokConnection.on('gift', (data) => {
        sendToOrchestrator('PURCHASE_EVENT', {
            session_id: sessionId,
            username: data.uniqueId,
            message: `🛒 @${data.uniqueId} sent ${data.giftName} x${data.repeatCount}!`,
            gift_name: data.giftName,
            gift_count: data.repeatCount,
            diamond_count: data.diamondCount,
            timestamp: new Date().toISOString(),
        });
    });

    // Viewer count
    tiktokConnection.on('roomUser', (data) => {
        sendToOrchestrator('VIEWER_COUNT', {
            session_id: sessionId,
            viewer_count: data.viewerCount,
            timestamp: new Date().toISOString(),
        });
    });

    // Disconnect
    tiktokConnection.on('disconnected', () => {
        console.log(`[ChatMonitor] TikTok stream disconnected (session: ${sessionId})`);
        activeSessions.delete(sessionId);
        sendToOrchestrator('MONITORING_DISCONNECTED', {
            session_id: sessionId,
        });
    });

    // Error
    tiktokConnection.on('error', (err) => {
        console.error(`[ChatMonitor] TikTok error (session: ${sessionId}):`, err.message);
    });
}

/**
 * Stop monitoring a TikTok live stream.
 */
function stopTikTokMonitoring(sessionId) {
    const connection = activeSessions.get(sessionId);
    if (connection) {
        connection.disconnect();
        activeSessions.delete(sessionId);
        console.log(`[ChatMonitor] Stopped monitoring session ${sessionId}`);
    }
}

// Start
connectToOrchestrator();

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log('[ChatMonitor] Shutting down...');
    for (const [sessionId, connection] of activeSessions) {
        connection.disconnect();
    }
    if (orchestratorWs) {
        orchestratorWs.close();
    }
    process.exit(0);
});

process.on('SIGINT', () => {
    process.emit('SIGTERM');
});
