import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogLabel,
  indexStatusLabel,
  fingerprintLabel,
  metadataLabel,
  retrievalLabel,
} from "./retrieval-labels.ts";

test("configured hybrid is not claimed as an executed semantic search", () => {
  assert.match(retrievalLabel("hybrid"), /配置目标/);
  assert.doesNotMatch(retrievalLabel("hybrid_rrf"), /配置目标/);
  assert.match(retrievalLabel("hybrid_rrf"), /向量融合/);
});

test("actual BM25 fallback remains lexical even when semantic was requested", () => {
  assert.match(retrievalLabel("bm25"), /BM25/);
  assert.doesNotMatch(retrievalLabel("bm25"), /向量/);
  assert.equal(retrievalLabel("keyword_baseline"), "关键词基线");
});

test("unknown retrieval modes never inherit an enabled claim", () => {
  for (const value of [undefined, null, {}, true, "llm", "<script>alert(1)</script>"]) {
    assert.equal(retrievalLabel(value), "尚未确认");
  }
});

test("provider catalog is not mislabeled as curated", () => {
  assert.equal(catalogLabel("mixed"), "含供应商导入的目录");
  assert.equal(catalogLabel("igdb"), "IGDB 游戏资料");
  assert.equal(catalogLabel("curated_starter"), "人工整理的起始目录");
  assert.equal(catalogLabel(undefined), "目录来源待确认");
});

test("metadata status does not imply verified hard constraints", () => {
  assert.match(metadataLabel("provider"), /待核验/);
  assert.equal(metadataLabel("curated"), "人工整理资料");
  assert.equal(metadataLabel("invalid"), "来源待确认");
});

test("only valid text fingerprints are abbreviated", () => {
  assert.equal(fingerprintLabel("a".repeat(64)), "a".repeat(12));
  for (const value of [undefined, 123, {}, "a".repeat(63), "z".repeat(64), "secret"]) {
    assert.equal(fingerprintLabel(value), null);
  }
});

test("persistent index configuration is not an executed semantic result", () => {
  assert.equal(retrievalLabel("indexed_hybrid"), "持久索引混合检索（配置目标）");
  assert.equal(retrievalLabel("indexed_hybrid_rrf"), "词项 + 持久向量索引");
});

test("ready index is not a model-service health claim", () => {
  assert.match(indexStatusLabel("ready"), /未探测/);
  assert.match(indexStatusLabel("stale"), /重建/);
  assert.match(indexStatusLabel("model_mismatch"), /不一致/);
  assert.equal(indexStatusLabel("unknown-new-status"), "索引状态待确认");
});
