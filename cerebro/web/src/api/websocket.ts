import { getAccessToken } from './auth';

type WebSocketMessage = {
    type: string;
    data?: unknown;
    project_id?: string | null;
};

type Listener = (data: unknown, projectId: string | null) => void;

function isWebSocketMessage(message: unknown): message is WebSocketMessage {
    return (
        typeof message === "object" &&
        message !== null &&
        "type" in message &&
        typeof (message as { type?: unknown }).type === "string"
    );
}

// RFC 6455 policy violation: what the server closes with when it rejects the token.
const AUTH_REJECTED = 1008;

export class WebSocketManager {
    private ws: WebSocket | null = null;
    private url: string;
    private reconnectAttempts = 0;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private closedByClient = false;
    private maxReconnectAttempts = 5;
    private listeners: Map<string, Set<Listener>> = new Map();
    private openListeners: Set<() => void> = new Set();

    constructor(url: string) {
        this.url = url;
    }

    connect() {
        this.closedByClient = false;
        this.clearReconnectTimer();
        this.ws?.close();
        // The server authenticates WebSockets by ?token=, never by header.
        const token = getAccessToken();
        const url = new URL(this.url);
        if (token) url.searchParams.set('token', token);
        const socket = new WebSocket(url);
        this.ws = socket;

        socket.onopen = () => {
            this.openListeners.forEach((callback) => callback());
        };

        // The server accepts before it authenticates, so an open socket proves nothing:
        // only a frame from the server resets the retry budget.
        socket.onmessage = (event) => {
            let message: unknown;
            try {
                message = JSON.parse(event.data);
            } catch {
                return;
            }
            if (isWebSocketMessage(message)) {
                this.reconnectAttempts = 0;
                this.notifyListeners(message.type, message.data, message.project_id ?? null);
            }
        };

        socket.onclose = (event) => {
            // A superseded or deliberately closed socket must not resurrect itself,
            // and a rejected token will not become valid by retrying.
            if (this.ws === socket && !this.closedByClient && event.code !== AUTH_REJECTED) {
                this.attemptReconnect();
            }
        };

        socket.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    private attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            this.reconnectTimer = setTimeout(() => this.connect(), 1000 * this.reconnectAttempts);
        }
    }

    private clearReconnectTimer() {
        if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
    }

    subscribe<TData = unknown>(
        type: string,
        callback: (data: TData, projectId: string | null) => void,
    ) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, new Set());
        }
        const listener = callback as Listener;
        this.listeners.get(type)!.add(listener);

        return () => {
            this.listeners.get(type)?.delete(listener);
        };
    }

    /** Runs on every (re)connect, so callers can re-send their subscription frames. */
    onOpen(callback: () => void) {
        this.openListeners.add(callback);
        return () => {
            this.openListeners.delete(callback);
        };
    }

    private notifyListeners(type: string, data: unknown, projectId: string | null) {
        this.listeners.get(type)?.forEach((callback) => callback(data, projectId));
    }

    /** Frames are never queued: callers re-send from `onOpen`, so a stale one cannot leak across screens. */
    send(message: WebSocketMessage) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    disconnect() {
        this.closedByClient = true;
        this.clearReconnectTimer();
        this.ws?.close();
    }
}

const getWsUrl = () => {
    if (import.meta.env.VITE_WS_URL) {
        return import.meta.env.VITE_WS_URL;
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname === 'localhost' ? 'localhost:8000' : window.location.host;
    return `${protocol}//${host}/ws`;
};

export const wsManager = new WebSocketManager(getWsUrl());
