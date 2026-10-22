import assert from "node:assert/strict";
import test from "node:test";
import { metadataStatusLabel } from "./metadata-enrichment.ts";

test("configured, disabled and unknown states do not claim complete coverage", () => {
  assert.match(metadataStatusLabel("disabled"), /未启用/);
  assert.match(metadataStatusLabel("not_configured"), /缺少/);
  assert.match(metadataStatusLabel("unexpected"), /未知/);
});
test("a recent successful batch is not full catalog completion", () => {
  assert.match(metadataStatusLabel("ready"), /不代表全库/);
  assert.match(metadataStatusLabel("processing"), /一个补全批次/);
});
