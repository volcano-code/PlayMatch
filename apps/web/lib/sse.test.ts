import assert from "node:assert/strict";
import test from "node:test";
import { SseDecoder } from "./sse.ts";
import { moneyToMinor, safeExternalUrl } from "./api.ts";

test("SSE preserves split Chinese UTF-8 and reconnect sequence IDs", () => {
  const parser = new SseDecoder();
  const bytes = new TextEncoder().encode(
    ': ping\r\n\r\nid: 3\r\nevent: progress\r\ndata: {"message":"正在检索"}\r\n\r\n',
  );
  const result = [];
  for (const byte of bytes) result.push(...parser.feed(new Uint8Array([byte])));
  assert.deepEqual(result, [{ event: "progress", data: '{"message":"正在检索"}', id: "3" }]);
});

test("SSE supports multiple events and multiline data", () => {
  const result = new SseDecoder().feed(
    new TextEncoder().encode("data: first\ndata: second\n\nevent: done\ndata: {}\n\n"),
  );
  assert.equal(result[0].data, "first\nsecond");
  assert.equal(result[1].event, "done");
});

test("budget input uses integer minor units and rejects exponent/negative values", () => {
  assert.equal(moneyToMinor("19.99"), 1999);
  assert.equal(moneyToMinor("0.1"), 10);
  assert.equal(moneyToMinor(""), null);
  assert.throws(() => moneyToMinor("-1"));
  assert.throws(() => moneyToMinor("1e5"));
  assert.throws(() => moneyToMinor("2.001"));
});

test("external evidence cannot create script links", () => {
  assert.equal(safeExternalUrl("javascript:alert(1)"), undefined);
  assert.equal(
    safeExternalUrl("https://store.steampowered.com/app/10/"),
    "https://store.steampowered.com/app/10/",
  );
});
