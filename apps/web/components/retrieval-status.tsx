"use client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Capabilities, IndexStatus, Run } from "@/lib/types";
import { fingerprintLabel, indexStatusLabel, retrievalLabel } from "@/lib/retrieval-labels";

export function RetrievalStatus({
  run,
  capabilities,
}: {
  run: Run | null;
  capabilities?: Capabilities;
}) {
  const actual = run?.intent?.retrieval;
  const fingerprint = fingerprintLabel(run?.intent?.catalog_fingerprint);
  const index = useQuery({
    queryKey: ["retrieval-index"],
    queryFn: () => api<IndexStatus>("/retrieval/index"),
    enabled: capabilities?.retrieval_mode === "indexed_hybrid",
    staleTime: 0,
    retry: false,
  });
  const usedIndex = typeof run?.intent?.index_id === "string" ? run.intent.index_id : null;
  return (
    <div className="notice" data-testid="retrieval-status" aria-label="检索方式与范围">
      <div>
        <strong>
          {run
            ? `本次检索：${retrievalLabel(actual)}`
            : `准备使用：${retrievalLabel(capabilities?.retrieval_mode)}`}
        </strong>
        <p>
          检索负责找候选；语言、人数、平台和购买条件仍需逐项核验。排序分数不是匹配概率。
          {!run && capabilities?.retrieval_mode === "hybrid"
            ? capabilities.embedding_configured
              ? " 向量接口已配置，实际执行或降级结果以任务为准。"
              : " 向量接口未配置，发起推荐时会明确降级。"
            : ""}
        </p>
        {capabilities?.retrieval_mode === "indexed_hybrid" && (
          <div data-testid="index-status">
            <p>
              {index.isError
                ? "索引状态读取失败，不能据此认定服务可用。"
                : indexStatusLabel(index.data?.status)}
            </p>
            {index.data && (
              <p>
                索引文档 {index.data.document_count} 条 · 版本代数 {index.data.generation} ·
                精确余弦检索
              </p>
            )}
            <button
              className="text-button"
              onClick={() => index.refetch()}
              disabled={index.isFetching}
            >
              刷新索引状态
            </button>
          </div>
        )}
        {fingerprint && <p>检索文本版本：{fingerprint}</p>}
        {usedIndex && <p>本次使用的索引：{usedIndex}</p>}
      </div>
    </div>
  );
}
