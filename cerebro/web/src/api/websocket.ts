import { getAccessToken } from './auth';

type WebSocketMessage = {
    type: string;
    data?: unknown;
};

function isWebSocketMessage(message: unknown): message is WebSocketMessage {
    return (
        typeof message === "object" &&
        message !== null &&
        "type" in message &&
        typeof (message as { type?: unknown }).type === "string"
    );
}

export class WebSocketManager {
    private ws: WebSocket | null = null;
    private url: string;
    private reconnectAttempts = 0;
    private closedByClient = false;
    private maxReconnectAttempts = 5;
    private listeners: Map<string, Set<(data: unknown) => void>> = new Map();
    private pendingMessages: WebSocketMessage[] = [];

    constructor(url: string) {
        this.url = url;
    }

    connect() {
        this.closedByClient = false;
        this.ws?.close();
        // The server authenticates WebSockets by ?token=, never by header.
        const token = getAccessToken();
        const url = new URL(this.url);
        if (token) url.searchParams.set('token', token);
        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            this.reconnectAttempts = 0;
            this.flushPendingMessages();
            console.log('WebSocket connected');
        };

        this.ws.onmessage = (event) => {
            const message: unknown = JSON.parse(event.data);
            if (isWebSocketMessage(message)) {
                this.notifyListeners(message.type, message.data);
            }
        };

        const socket = this.ws;
        this.ws.onclose = () => {
            // A superseded or deliberately closed socket must not resurrect itself.
            if (this.ws === socket && !this.closedByClient) this.attemptReconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    private attemptReconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            setTimeout(() => this.connect(), 1000 * this.reconnectAttempts);
        }
    }

    subscribe<TData = unknown>(type: string, callback: (data: TData) => void) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, new Set());
        }
        const listener = callback as (data: unknown) => void;
        this.listeners.get(type)!.add(listener);

        return () => {
            this.listeners.get(type)?.delete(listener);
        };
    }

    private notifyListeners(type: string, data: unknown) {
        this.listeners.get(type)?.forEach((callback) => callback(data));
    }

    private flushPendingMessages() {
        const ws = this.ws;
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            return;
        }

        const messages = [...this.pendingMessages];
        this.pendingMessages = [];
        messages.forEach((message) => ws.send(JSON.stringify(message)));
    }

    send(message: WebSocketMessage) {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            return;
        }

        this.pendingMessages.push(message);
    }

    disconnect() {
        this.closedByClient = true;
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
