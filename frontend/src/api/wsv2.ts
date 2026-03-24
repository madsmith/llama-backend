type JsonMessage = Record<string, unknown>;
type MessageHandler = (msg: JsonMessage) => void;
type ConnectHandler = () => void;

export interface WsV2Client {
  subscribe(
    msgType: string,
    handler: MessageHandler,
    onConnect?: ConnectHandler,
  ): () => void;

  send(msg: JsonMessage): void;
}

class Subscription {
  public readonly msgType: string;
  public readonly handler: MessageHandler;
  public readonly onConnect?: ConnectHandler;

  constructor(
    msgType: string,
    handler: MessageHandler,
    onConnect?: ConnectHandler,
  ) {
    this.msgType = msgType;
    this.handler = handler;
    this.onConnect = onConnect;
  }
}

class WsV2ClientImpl implements WsV2Client {
  private readonly handlers = new Map<string, Set<Subscription>>();
  private readonly onConnectSubscriptions = new Set<Subscription>();

  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // ---- public API ----

  subscribe(
    msgType: string,
    handler: MessageHandler,
    onConnect?: ConnectHandler,
  ): () => void {
    console.log("Subscribing to message type:", msgType);
    const sub = new Subscription(msgType, handler, onConnect);

    let bucket = this.handlers.get(msgType);
    if (!bucket) {
      bucket = new Set();
      this.handlers.set(msgType, bucket);
    }
    bucket.add(sub);

    if (onConnect) {
      this.onConnectSubscriptions.add(sub);
      if (this.isOpen()) onConnect();
    }

    this.connect();
    return () => this.unsubscribe(sub);
  }

  send(msg: JsonMessage): void {
    console.log("Sending message:", msg);
    if (this.isOpen()) {
      this.ws!.send(JSON.stringify(msg));
    }
  }

  // ---- connection lifecycle ----

  public connect(): void {
    console.log("Connecting to WebSocket");
    if (this.isOpen() || this.isConnecting() || this.reconnectTimer) return;
    console.log("Creating new WebSocket connection");

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.ws = new WebSocket(`${proto}//${location.host}/v2/ws/manager`);

    this.ws.onopen = this._wsOpenHandler;
    this.ws.onmessage = this._wsMessageHandler;
    this.ws.onclose = this._wsCloseHandler;
  }

  // ---- event handlers (clean, reusable) ----

  private _wsOpenHandler = (): void => {
    console.log("WebSocket opened");
    for (const sub of this.onConnectSubscriptions) {
      sub.onConnect?.();
    }
  };

  private _wsMessageHandler = (event: MessageEvent): void => {
    console.log("Received message:", event.data);
    const msg = this.parseMessage(event.data);
    if (!msg) return;

    const type = this.getMessageType(msg);
    if (!type) return;

    const bucket = this.handlers.get(type);
    if (!bucket) return;

    for (const sub of bucket) {
      sub.handler(msg);
    }
  };

  private _wsCloseHandler = (): void => {
    console.log("WebSocket closed");
    this.ws = null;

    if (!this.hasSubscribers()) return;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 2000);
  };

  // ---- internals ----

  private unsubscribe(sub: Subscription): void {
    const bucket = this.handlers.get(sub.msgType);
    if (bucket) {
      bucket.delete(sub);
      if (bucket.size === 0) this.handlers.delete(sub.msgType);
    }

    if (sub.onConnect) {
      this.onConnectSubscriptions.delete(sub);
    }
  }

  private parseMessage(data: unknown): JsonMessage | null {
    if (typeof data !== "string") return null;
    try {
      const parsed = JSON.parse(data);
      return typeof parsed === "object" && parsed ? parsed : null;
    } catch {
      return null;
    }
  }

  private getMessageType(msg: JsonMessage): string | null {
    return typeof msg.msg === "string" ? msg.msg : null;
  }

  private hasSubscribers(): boolean {
    return this.handlers.size > 0 || this.onConnectSubscriptions.size > 0;
  }

  private isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  private isConnecting(): boolean {
    return this.ws?.readyState === WebSocket.CONNECTING;
  }
}

let instance: WsV2Client | null = null;

export function getWsV2(): WsV2Client {
  if (!instance) {
    console.log("Creating new WsV2Client instance");
    const impl = new WsV2ClientImpl();
    impl.connect();
    instance = impl;
  }
  console.log("Returning WsV2Client instance");
  return instance!;
}