export class WebSocketManager {
    private ws: WebSocket | null = null;
    private url: string;
    private reconnectAttempts = 0;
    private maxReconnectAttempts = 5;
    private listeners: Map<string, Set<(data: any) => void>> = new Map();

    constructor(url: string) {
        this.url = url;
    }

    connect() {
        this.ws = new WebSocket(this.url);

        this.ws.onopen = () => {
            this.reconnectAttempts = 0;
            console.log('WebSocket connected');
        };

        this.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            this.notifyListeners(message.type, message.data);
        };

        this.ws.onclose = () => {
            this.attemptReconnect();
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

    subscribe(type: string, callback: (data: any) => void) {
        if (!this.listeners.has(type)) {
            this.listeners.set(type, new Set());
        }
        this.listeners.get(type)!.add(callback);

        return () => {
            this.listeners.get(type)?.delete(callback);
        };
    }

    private notifyListeners(type: string, data: any) {
        this.listeners.get(type)?.forEach((callback) => callback(data));
    }

    send(message: any) {
        this.ws?.send(JSON.stringify(message));
    }

    disconnect() {
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
