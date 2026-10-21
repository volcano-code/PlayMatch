import assert from "node:assert/strict";
import test from "node:test";
import { readRunReference, terminalRun, RUN_REFERENCE_TTL } from "./run-reference.ts";
const now = 10000000;
const reference = {
  id: "12345678-1234-1234-1234-123456789012",
  access_token: "a".repeat(43),
  owner: null,
  saved_at: now,
};
test("anonymous task resumes with a valid per-tab capability, not a new POST", () => {
  assert.deepEqual(readRunReference(JSON.stringify(reference), null, now), reference);
});
test("task references cannot cross accounts or survive expiry", () => {
  assert.equal(readRunReference(JSON.stringify(reference), "user-a", now), null);
  assert.equal(
    readRunReference(
      JSON.stringify({ ...reference, owner: "user-a", access_token: null }),
      "user-b",
      now,
    ),
    null,
  );
  assert.equal(
    readRunReference(JSON.stringify(reference), null, now + RUN_REFERENCE_TTL + 1),
    null,
  );
  assert.equal(readRunReference(JSON.stringify(reference), null, now - 1), null);
});
test("invalid ids, tokens and malformed state are rejected", () => {
  for (const raw of [
    "{}",
    "null",
    "broken",
    "x".repeat(2049),
    JSON.stringify({ ...reference, id: "../else" }),
    JSON.stringify({ ...reference, access_token: null }),
  ]) {
    assert.equal(readRunReference(raw, null, now), null);
  }
});
test("private query and unknown fields are not restored", () => {
  assert.deepEqual(
    readRunReference(JSON.stringify({ ...reference, query: "private" }), null, now),
    reference,
  );
});
test("cancelled is terminal, queued and running are not", () => {
  assert.equal(terminalRun("cancelled"), true);
  assert.equal(terminalRun("queued"), false);
  assert.equal(terminalRun("running"), false);
});
