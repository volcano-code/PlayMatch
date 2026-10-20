import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!path.length || path.some((part) => !/^[a-zA-Z0-9_-]+$/.test(part))) {
    return Response.json({ detail: "无效的接口路径。" }, { status: 400 });
  }
  const origin = new URL(process.env.API_INTERNAL_URL || "http://127.0.0.1:8000");
  if (!["http:", "https:"].includes(origin.protocol) || origin.username || origin.password) {
    return Response.json({ detail: "服务配置暂不可用。" }, { status: 503 });
  }
  // The upstream host comes only from server configuration, never from browser input.
  const upstream = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, origin);
  upstream.search = request.nextUrl.search;
  const headers = new Headers();
  for (const name of [
    "accept",
    "content-type",
    "cookie",
    "idempotency-key",
    "x-run-token",
    "last-event-id",
    "origin",
    "sec-fetch-site",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    let body: ArrayBuffer | undefined;
    if (!["GET", "HEAD"].includes(request.method) && request.body) {
      const limit = 64 * 1024;
      if (Number(request.headers.get("content-length") || 0) > limit) {
        return Response.json({ detail: "请求内容过大，请缩短后重试。" }, { status: 413 });
      }
      const reader = request.body.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        length += next.value.byteLength;
        if (length > limit) {
          await reader.cancel();
          return Response.json({ detail: "请求内容过大，请缩短后重试。" }, { status: 413 });
        }
        chunks.push(next.value);
      }
      const joined = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) {
        joined.set(chunk, offset);
        offset += chunk.byteLength;
      }
      body = joined.buffer;
    }
    const response = await fetch(upstream, {
      method: request.method,
      headers,
      cache: "no-store",
      redirect: "manual",
      body,
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(120_000)]),
    });
    const outgoing = new Headers();
    for (const name of ["content-type", "retry-after", "x-request-id"]) {
      const value = response.headers.get(name);
      if (value) outgoing.set(name, value);
    }
    for (const cookie of response.headers.getSetCookie()) outgoing.append("set-cookie", cookie);
    outgoing.set("cache-control", "no-store");
    outgoing.set("x-content-type-options", "nosniff");
    if (response.headers.get("content-type")?.includes("text/event-stream"))
      outgoing.set("x-accel-buffering", "no");
    return new Response(response.status === 204 ? null : response.body, {
      status: response.status,
      headers: outgoing,
    });
  } catch {
    return Response.json(
      { detail: "暂时连接不到推荐服务。请稍后重试，或检查服务状态。" },
      { status: 503 },
    );
  }
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE, proxy as PUT };
