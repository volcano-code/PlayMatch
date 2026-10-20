export function retrievalLabel(value: unknown): string {
  switch (value) {
    case "keyword_baseline":
      return "关键词基线";
    case "bm25":
      return "BM25 词项检索";
    case "hybrid_rrf":
      return "词项 + 向量融合";
    case "hybrid":
      return "混合检索（配置目标）";
    default:
      return "尚未确认";
  }
}

export function catalogLabel(value: unknown): string {
  switch (value) {
    case "curated_starter":
      return "人工整理的起始目录";
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
  if (value === "curated") return "人工整理资料";
  return "来源待确认";
}

export function fingerprintLabel(value: unknown): string | null {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value)
    ? value.slice(0, 12)
    : null;
}
