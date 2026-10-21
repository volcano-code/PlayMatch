export function retrievalLabel(value: unknown): string {
  switch (value) {
    case "keyword_baseline":
      return "关键词基线";
    case "bm25":
      return "BM25 词项检索";
    case "hybrid_rrf":
      return "词项 + 向量融合";
    case "indexed_hybrid_rrf":
      return "词项 + 持久向量索引";
    case "indexed_hybrid":
      return "持久索引混合检索（配置目标）";
    case "hybrid":
      return "混合检索（配置目标）";
    default:
      return "尚未确认";
  }
}

export function catalogLabel(value: unknown): string {
  switch (value) {
    case "curated_starter":
      return "来源整理的游戏目录";
    case "mixed":
      return "含供应商导入的目录";
    case "igdb":
      return "IGDB 游戏资料";
    default:
      return "目录来源待确认";
  }
}

export function metadataLabel(value: unknown): string {
  if (value === "provider") return "供应商资料 · 硬条件待核验";
  if (value === "curated") return "来源整理资料";
  return "来源待确认";
}

export function fingerprintLabel(value: unknown): string | null {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value) ? value.slice(0, 12) : null;
}

export function indexStatusLabel(value: unknown): string {
  switch (value) {
    case "ready":
      return "索引就绪（未探测向量服务）";
    case "missing":
      return "尚未发布索引，推荐将降级";
    case "stale":
      return "目录已变更，需要重建索引";
    case "model_mismatch":
      return "模型版本与索引不一致";
    case "not_configured":
      return "向量接口或不可变模型版本未配置";
    case "corrupt":
      return "索引不可用，需要管理员检查";
    default:
      return "索引状态待确认";
  }
}
