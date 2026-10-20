import type { Capabilities, Run } from "@/lib/types";
import { fingerprintLabel, retrievalLabel } from "@/lib/retrieval-labels";

export function RetrievalStatus({
  run,
  capabilities,
}: {
  run: Run | null;
  capabilities?: Capabilities;
}) {
  const actual = run?.intent?.retrieval;
  const fingerprint = fingerprintLabel(run?.intent?.catalog_fingerprint);
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
        {fingerprint && <p>检索文本版本：{fingerprint}</p>}
      </div>
    </div>
  );
}
