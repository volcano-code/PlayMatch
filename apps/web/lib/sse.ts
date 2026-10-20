export interface ServerEvent {
  event: string;
  data: string;
  id: string | null;
}

/** Incremental SSE decoder: handles split UTF-8 characters, CRLF, comments and multiline data. */
export class SseDecoder {
  private decoder = new TextDecoder();
  private buffer = "";
  feed(chunk: Uint8Array, final = false): ServerEvent[] {
    this.buffer += this.decoder.decode(chunk, { stream: !final });
    const events: ServerEvent[] = [];
    // Normalise complete CRLF pairs only; a trailing CR may belong to the next chunk.
    this.buffer = this.buffer.replace(/\r\n/g, "\n");
    let boundary: number;
    while ((boundary = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      let event = "message";
      let id: string | null = null;
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const split = line.indexOf(":");
        const field = split < 0 ? line : line.slice(0, split);
        const value = split < 0 ? "" : line.slice(split + 1).replace(/^ /, "");
        if (field === "event") event = value;
        if (field === "id" && !value.includes("\0")) id = value;
        if (field === "data") data.push(value);
      }
      if (data.length) events.push({ event, data: data.join("\n"), id });
    }
    return events;
  }
}

export async function streamRun(
  id: string,
  token: string | null,
  onEvent: (event: ServerEvent) => void,
  signal: AbortSignal,
) {
  let lastEventId: string | null = null;
  let done = false;
  for (let attempt = 0; attempt < 2 && !done; attempt++) {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(id)}/events`, {
      credentials: "same-origin",
      cache: "no-store",
      signal,
      headers: {
        Accept: "text/event-stream",
        ...(token ? { "X-Run-Token": token } : {}),
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
    });
    if (!response.ok || !response.body) throw new Error("进度连接暂不可用，正在读取最终结果。");
    const reader = response.body.getReader();
    const parser = new SseDecoder();
    try {
      while (!done) {
        const chunk = await reader.read();
        const events = parser.feed(chunk.value || new Uint8Array(), chunk.done);
        for (const event of events) {
          if (event.id) lastEventId = event.id;
          onEvent(event);
          if (event.event === "done") done = true;
        }
        if (chunk.done) break;
      }
    } finally {
      await reader.cancel().catch(() => undefined);
    }
  }
}
