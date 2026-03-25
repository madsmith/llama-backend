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

  /**
   * Subscribe to generic server-pushed events of *type* with correlation *id*.
   * Sends `subscribe_event` on connect and `unsubscribe_event` on cleanup.
   * Returns a React-compatible cleanup function.
   */
  subscribeToEvent(
    type: string,
    id: string,
    handler: (eventData: Record<string, unknown>) => void,
  ): () => void;
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
  // FIFO queue matching subscribe_event_response messages to subscribeToEvent callers.
  private readonly _pendingEventSubs: Array<(subscriptionId: number) => void> = [];

  private ws: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  // ---- public API ----

  subscribe(
    msgType: string,
    handler: MessageHandler,
    onConnect?: ConnectHandler,
  ): () => void {
    console.log("Subscribe:", msgType);
    const subscription = new Subscription(msgType, handler, onConnect);

    let bucket = this.handlers.get(msgType);
    if (!bucket) {
      bucket = new Set();
      this.handlers.set(msgType, bucket);
    }
    bucket.add(subscription);

    if (onConnect) {
      this.onConnectSubscriptions.add(subscription);
      if (this.isOpen()) {
        // Defer so React StrictMode's synchronous cleanup can remove this
        // subscription before the callback fires. The has() guard ensures a
        // stale sub from the first (discarded) StrictMode mount doesn't send.
        queueMicrotask(() => {
          if (this.onConnectSubscriptions.has(subscription)) {
            onConnect();
          }
        });
      }
    }

    this.connect();
    return () => this.unsubscribe(subscription);
  }

  send(msg: JsonMessage): void {
    console.log("Sending message:", msg);
    if (this.isOpen()) {
      this.ws!.send(JSON.stringify(msg));
    }
  }

  // ---- connection lifecycle ----

  public connect(): void {
    if (this.isOpen() || this.isConnecting() || this.reconnectTimer) return;

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    this.ws = new WebSocket(`${proto}//${location.host}/v2/ws/manager`);

    this.ws.onopen = this._wsOpenHandler;
    this.ws.onmessage = this._wsMessageHandler;
    this.ws.onclose = this._wsCloseHandler;
  }

  // ---- event handlers (clean, reusable) ----

  private _wsOpenHandler = (): void => {
    for (const sub of this.onConnectSubscriptions) {
      sub.onConnect?.();
    }
  };

  private _wsMessageHandler = (event: MessageEvent): void => {
    const msg = this.parseMessage(event.data);
    if (!msg) return;

    const type = this.getMessageType(msg);
    if (!type) return;

    console.log("Received:", type, msg);

    // Route subscribe_event_response to the FIFO pending queue.
    if (type === "subscribe_event_response") {
      const subId = typeof msg.subscription_id === "number" ? msg.subscription_id : -1;
      this._pendingEventSubs.shift()?.(subId);
      return;
    }

    const bucket = this.handlers.get(type);
    if (!bucket) return;

    for (const sub of bucket) {
      console.log("Dispatch message:", sub);
      sub.handler(msg);
    }
  };

  subscribeToEvent(
    type: string,
    id: string,
    handler: (eventData: Record<string, unknown>) => void,
  ): () => void {
    let subscriptionId: number | null = null;
    let pendingCallback: ((subId: number) => void) | null = null;

    const eventHandler = (msg: JsonMessage) => {
      if (msg.type !== type || msg.id !== id) return;
      handler(msg.event_data as Record<string, unknown>);
    };

    const onConnect = () => {
      subscriptionId = null;
      pendingCallback = (subId: number) => {
        subscriptionId = subId;
      };
      this._pendingEventSubs.push(pendingCallback);
      this.send({ msg: "subscribe_event", type, id });
    };

    const unsub = this.subscribe("event", eventHandler, onConnect);

    return () => {
      unsub();
      if (pendingCallback !== null) {
        const idx = this._pendingEventSubs.indexOf(pendingCallback);
        if (idx !== -1) this._pendingEventSubs.splice(idx, 1);
      }
      if (subscriptionId !== null) {
        this.send({ msg: "unsubscribe_event", type, subscription_id: subscriptionId });
      }
    };
  }

  private _wsCloseHandler = (): void => {
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
    const impl = new WsV2ClientImpl();
    impl.connect();
    instance = impl;
  }
  return instance!;
}