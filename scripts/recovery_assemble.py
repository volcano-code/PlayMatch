"""One-shot deterministic recovery assembly; removed from the resulting candidate."""

from pathlib import Path


def replace(path, old, new):
    target = Path(path)
    source = target.read_text()
    if source.count(old) != 1:
        raise RuntimeError(f"Expected one exact patch anchor in {path}: {old[:70]!r}")
    target.write_text(source.replace(old, new))


def append(path, text):
    target = Path(path)
    target.write_text(target.read_text() + text)


replace("services/api/playmatch/config.py", "from urllib.parse import urlparse", "from urllib.parse import urlparse\nfrom typing import Literal")
replace("services/api/playmatch/config.py", "    @property\n    def llm_enabled", '''    retrieval_mode: Literal["keyword_baseline", "bm25", "hybrid"] = "bm25"
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_timeout_seconds: float = Field(default=8, ge=0.1, le=20, allow_inf_nan=False)
    semantic_min_score: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)

    @property
    def embedding_enabled(self) -> bool:
        try:
            parsed = urlparse(self.embedding_base_url)
            local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            return bool(
                self.embedding_model and parsed.hostname
                and (local or (parsed.scheme == "https" and self.embedding_api_key))
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            return False

    @property
    def llm_enabled''')
append("services/api/playmatch/models.py", '''

class CatalogLock(Base):
    __tablename__ = "catalog_lock"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class CatalogSource(Base):
    __tablename__ = "catalog_sources"
    provider_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), unique=True)
    observed_at: Mapped[str] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64))


class CatalogImport(Base):
    __tablename__ = "catalog_imports"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40))
    report: Mapped[dict] = mapped_column(JSON)
''')
workflow = "services/api/playmatch/workflow.py"
replace(workflow, "The initial release deliberately does not claim semantic embeddings are deployed.", "BM25 is offline by default; semantic retrieval requires an explicit operator configuration.")
replace(workflow, "from playmatch.config import Settings", "from playmatch.config import Settings\nfrom playmatch.retrieval import search_catalog")
replace(workflow, '        if word in query:\n            platform = target', '''        pattern = re.escape(word)
        if word.isascii():
            pattern = r"(?<![a-z0-9])" + pattern + r"(?![a-z0-9])"
        if re.search(pattern, query):
            platform = target''')
replace(workflow, '''        candidates = retrieve(
            state["catalog"], state["intent"], state["request"]["query"], state["feedback"]
        )
        progress("retrieve", f"已从 {len(state['catalog'])} 款人工整理游戏中检索候选", {})
        return {"candidates": candidates}''', '''        if settings.retrieval_mode == "keyword_baseline":
            candidates = retrieve(
                state["catalog"], state["intent"], state["request"]["query"], state["feedback"]
            )
            metadata, warnings = {"retrieval": "keyword_baseline"}, []
        else:
            candidates, metadata, warnings = search_catalog(
                state["catalog"], state["intent"], state["request"]["query"],
                state["feedback"], settings,
            )
        actual_intent = {**state["intent"], **metadata}
        progress("retrieve", f"已从 {len(state['catalog'])} 款目录游戏中检索候选", {"intent": actual_intent})
        return {"candidates": candidates, "intent": actual_intent, "warnings": state["warnings"] + warnings}''')
replace(workflow, '（根据人工整理的主题标签）。', '（根据目录主题标签；不代表硬条件已核验）。')
replace(workflow, '根据描述关键词与已有偏好提供的候选，建议查看详情再决定。', '根据本轮检索与已有偏好提供的候选，建议查看来源再决定。')
replace(workflow, '            reasons.append(game["description"])', '''            reasons.append(f"检索方式：{state['intent'].get('retrieval', 'keyword_baseline')}；排序分数不是匹配概率。")
            reasons.append(game["description"])''')
replace(workflow, '简介与主题来自人工整理；评分是当前候选排序分数，不是匹配概率。', '简介与主题来自目录记录；请查看来源，供应商记录不等于人工核验。')
replace("services/api/playmatch/runs.py", '"workflow_version": "0.1.0"', '"workflow_version": "0.2.0-recovery"')
replace("services/api/playmatch/runs.py", 'if not record.results or uncertain_budget or degraded_model', '''if not record.results or uncertain_budget or degraded_model or (
                        self.settings.retrieval_mode == "hybrid"
                        and record.intent.get("retrieval") != "hybrid_rrf"
                    )''')
main = "services/api/playmatch/main.py"
replace(main, 'from sqlalchemy import delete, func, select', 'from sqlalchemy import delete, func, select, update')
replace(main, 'from playmatch.models import (', 'from playmatch.models import (\n    CatalogLock,')
replace(main, '''        with database.sessions() as session:
            for raw in catalog''', '''        with database.sessions() as session:
            session.execute(update(CatalogLock).where(CatalogLock.id == 1).values(revision=CatalogLock.revision))
            for raw in catalog''')
replace(main, '"catalog_mode": "curated_starter",', '''"catalog_mode": "mixed" if any(
                row.payload.get("metadata_status") == "provider"
                for row in session.scalars(select(GameRecord))
            ) else "curated_starter",''')
replace(main, '"retrieval_mode": "keyword_baseline",', '''"retrieval_mode": settings.retrieval_mode,
            "embedding_configured": settings.embedding_enabled,
            "catalog_import": "operator_cli",
            "semantic_catalog_limit": 128,''')
replace(main, '当前为人工整理的起步目录，尚未启用全量 IGDB 同步。', '目录包含人工起步记录及可选的审核导入；尚未启用全量 IGDB 同步。')
replace(main, '当前检索是关键词与主题规则基线，尚未上线向量检索。', '默认 BM25；向量融合需显式配置，实际模式与降级原因见每次任务。')
replace("apps/web/lib/types.ts", 'catalog_mode: "curated_starter" | "igdb";', '''catalog_mode: "curated_starter" | "igdb" | "mixed";
  retrieval_mode: "keyword_baseline" | "bm25" | "hybrid";
  embedding_configured: boolean;
  catalog_import: "operator_cli";
  semantic_catalog_limit: number;''')
# Preserve historical evaluation labels. Run both modes explicitly in CI.
eval_path = "scripts/evaluate_recommendations.py"
replace(eval_path, 'def evaluate(dataset: dict) -> dict:', 'def evaluate(dataset: dict, retrieval_mode: str = "keyword_baseline") -> dict:')
replace(eval_path, 'Settings(_env_file=None, llm_api_key="", llm_model="")', 'Settings(_env_file=None, llm_api_key="", llm_model="", retrieval_mode=retrieval_mode)')
replace(eval_path, '"mode": "rules",\n        "network": "blocked",', '"mode": "rules",\n        "retrieval_mode": retrieval_mode,\n        "network": "blocked",')
replace(eval_path, 'This evaluates the keyword baseline only, not LLM, embeddings, live prices or purchase eligibility.', 'This evaluates the selected lexical ranker only, not LLM, embeddings, live prices or purchase eligibility.')
replace(eval_path, '    args = parser.parse_args()', '    parser.add_argument("--mode", choices=["keyword_baseline", "bm25"], default="keyword_baseline")\n    args = parser.parse_args()')
replace(eval_path, 'report = evaluate(dataset)', 'report = evaluate(dataset, args.mode)')
append(".env.example", '''
# Retrieval is separate from LLM intent parsing; no embedding network calls by default.
RETRIEVAL_MODE=bm25
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=
EMBEDDING_API_KEY=
EMBEDDING_TIMEOUT_SECONDS=8
SEMANTIC_MIN_SCORE=0.65
''')
append("docs/implementation-contract.md", '''

## v0.2 recovery extension

Capabilities adds `retrieval_mode:keyword_baseline|bm25|hybrid` (configured),
`embedding_configured:bool`, `catalog_import:operator_cli`, `semantic_catalog_limit:128`.
`catalog_mode` additionally supports `mixed` when provider entries exist. Run intent
records actual `retrieval:keyword_baseline|bm25|hybrid_rrf` and the retrieval text
fingerprint. Existing Game/Run/Recommendation public shapes remain unchanged.
All hard-condition and pricing constraints still apply after retrieval. Downgraded
hybrid requests are partial, not successful semantic runs. Import is CLI-only,
transactional, preview by default, with immutable identities and curated preservation.
See [implementation and boundaries](retrieval-import-v02.md).
''')
append("README.md", '''

## v0.2 恢复开发增量

默认检索升级为离线 BM25；原关键词模式保留用于对照。显式配置才使用 embedding + RRF，
失败会报告降级，真实向量质量仍待独立验收。新增审核后 IGDB 规范化记录的事务性 CLI 导入，
默认仅预演；相同键重复执行不会重复写入，冲突整批回滚，人工记录及已有游戏 ID 不覆盖。
原首轮说明是历史基线；本阶段行为与限制以 [v0.2 说明](docs/retrieval-import-v02.md) 为准。
''')
# CI uses a disposable PostgreSQL instance; do not run this fixture on user data.
ci = ".github/workflows/ci.yml"
replace(ci, '      - name: Check exported OpenAPI is current', '''      - name: Check BM25 against the unchanged exploratory cases
        run: uv run python scripts/evaluate_recommendations.py --mode bm25 --check --output /tmp/bm25-results.json
      - name: Verify atomic catalog imports on PostgreSQL
        env:
          CATALOG_TEST_DATABASE_URL: postgresql+psycopg://playmatch:ci-only-password@127.0.0.1:5432/playmatch_test
        run: uv run pytest services/api/tests/test_catalog_v02.py -q
      - name: Check exported OpenAPI is current''')
# Delete one-shot recovery helpers from the produced source candidate.
for path in (".github/workflows/dev-validation-tools.yml", ".github/workflows/recovery-workbench.yml", "scripts/recovery_assemble.py"):
    Path(path).unlink()
print("Recovery assembly completed; temporary workflow and assembler removed.")
