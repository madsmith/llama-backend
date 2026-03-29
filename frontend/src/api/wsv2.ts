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
   * One-shot request/response: sends *requestMsg* on connect, resolves with
   * the first response of *responseType*, then unsubscribes.
   */
  sendRequest<T extends JsonMessage>(
    requestMsg: JsonMessage,
    responseType: string,
  ): Promise<T>;

  /**
   * Subscribe to generic server-pushed events of *type* with correlation *id*.
   * Sends `subscribe_event` on connect and `unsubscribe_event` on cleanup.
   * Returns a React-compatible cleanup function.
   *
   * Pass `id: null` for events with no correlation id (e.g. proxy log events).
   * Pass `subType` when the protocol requires a `subtype` field (e.g. log events).
   */
  subscribeToEvent(
    type: string,
    id: string | null,
    handler: (eventData: Record<string, unknown>) => void,
    subType?: string,
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

  sendRequest<T extends JsonMessage>(
    requestMsg: JsonMessage,
    responseType: string,
  ): Promise<T> {
    return new Promise((resolve) => {
      const onResponse = (msg: JsonMessage) => {
        unsub();
        resolve(msg as T);
      };
      const onConnect = () => {
        console.log("Sending request:", requestMsg);
        this.send(requestMsg);
      };
      const unsub = this.subscribe(responseType, onResponse, onConnect);
    });
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
      sub.handler(msg);
    }
  };

  subscribeToEvent(
    type: string,
    id: string | null,
    handler: (eventData: Record<string, unknown>) => void,
    subType?: string,
  ): () => void {
    let subscriptionId: number | null = null;
    let pendingCallback: ((subId: number) => void) | null = null;

    const eventHandler = (msg: JsonMessage) => {
      if (msg.type !== type) return;
      if (id !== null && msg.id !== id) return;
      if (subType !== undefined && msg.subtype !== subType) return;
      handler(msg.data as Record<string, unknown>);
    };

    const onConnect = () => {
      subscriptionId = null;
      pendingCallback = (subId: number) => {
        subscriptionId = subId;
      };
      this._pendingEventSubs.push(pendingCallback);
      const sendMsg: JsonMessage = { msg: "subscribe_event", type };
      if (id !== null) sendMsg.id = id;
      if (subType !== undefined) sendMsg.subtype = subType;
      console.log("Sending subscribe_event:", sendMsg);
      this.send(sendMsg);
    };

    const unsub = this.subscribe("event", eventHandler, onConnect);

    return () => {
      unsub();
      if (pendingCallback !== null) {
        const idx = this._pendingEventSubs.indexOf(pendingCallback);
        if (idx !== -1) this._pendingEventSubs.splice(idx, 1);
      }
      if (subscriptionId !== null) {
        const unsubMsg: JsonMessage = { msg: "unsubscribe_event", type, subscription_id: subscriptionId };
        if (subType !== undefined) unsubMsg.subtype = subType;
        this.send(unsubMsg);
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