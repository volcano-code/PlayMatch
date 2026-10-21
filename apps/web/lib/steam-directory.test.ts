import test from "node:test";
import assert from "node:assert/strict";
import { steamQuery, steamStatusLabel } from "./steam-directory.ts";

test("configured or unknown state never implies completed synchronization", () => {
  assert.equal(steamStatusLabel("not_configured"), "未配置 Steam Key");
  assert.equal(steamStatusLabel("worker_not_running"), "未检测到同步进程心跳");
  assert.equal(steamStatusLabel("up_to_date"), "近期已完成接口遍历");
  assert.equal(steamStatusLabel("unrecognized"), "未知同步状态");
});
test("directory query is encoded and bounded to a read-only page", () => {
  const query = steamQuery(" A&B /  ", 1234);
  const parsed = new URL(query, "https://example.invalid");
  assert.equal(parsed.pathname, "/catalog/steam/games");
  assert.equal(parsed.searchParams.get("q"), "A&B /");
  assert.equal(parsed.searchParams.get("after_appid"), "1234");
  assert.equal(parsed.searchParams.get("limit"), "24");
  assert.equal(parsed.searchParams.has("key"), false);
});
