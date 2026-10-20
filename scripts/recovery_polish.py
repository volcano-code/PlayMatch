"""One-shot UI and delivery alignment; omitted from the final product tree."""
from pathlib import Path


def replace(path, old, new):
    file = Path(path)
    text = file.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"Patch anchor mismatch: {path}: {old[:50]}")
    file.write_text(text.replace(old, new))


app = "apps/web/components/playmatch-app.tsx"
replace(app, 'import { OfferDrawer } from "./offer-drawer";', 'import { OfferDrawer } from "./offer-drawer";\nimport { RetrievalStatus } from "./retrieval-status";\nimport { catalogLabel } from "@/lib/retrieval-labels";')
replace(app, '  const [catalogFilter, setCatalogFilter] = useState("");', '  const [catalogFilter, setCatalogFilter] = useState("");\n  const [catalogPage, setCatalogPage] = useState(0);')
replace(app, 'queryKey: ["games", catalogFilter],', 'queryKey: ["games", catalogFilter, catalogPage],')
replace(app, '`/games?limit=24${catalogFilter', '`/games?limit=24&offset=${catalogPage * 24}${catalogFilter')
replace(app, 'onClick={() => setCatalogFilter(tab.value)}', 'onClick={() => { setCatalogFilter(tab.value); setCatalogPage(0); }}')
replace(app, '                      setCatalogFilter("");', '                      setCatalogFilter("");\n                      setCatalogPage(0);')
replace(app, '''{capabilities.data?.catalog_mode === "igdb"
                      ? "IGDB 游戏资料"
                      : "人工整理的起始目录"}''', '''{catalogLabel(capabilities.data?.catalog_mode)}''')
replace(app, '              {busy && (', '              <RetrievalStatus run={run} capabilities={capabilities.data} />\n              {busy && (')
replace(app, '''              {!run && games.length > 0 && (
                <p className="catalog-footnote">''', '''              {!run && !busy && ((catalog.data?.total || 0) > 24 || catalogPage > 0) && (
                <nav className="catalog-tabs" aria-label="目录分页">
                  <button className="button button-secondary" disabled={catalogPage === 0 || catalog.isFetching}
                    onClick={() => setCatalogPage(page => Math.max(0, page - 1))}>上一页</button>
                  <span role="status" aria-live="polite">第 {catalogPage + 1} 页 · 共 {catalog.data?.total ?? "待加载"} 款</span>
                  <button className="button button-secondary"
                    disabled={catalog.isFetching || !catalog.data || (catalogPage + 1) * 24 >= catalog.data.total}
                    onClick={() => setCatalogPage(page => page + 1)}>下一页</button>
                </nav>
              )}
              {!run && games.length > 0 && (
                <p className="catalog-footnote">''')
replace(app, '当前展示 {games.length} 款目录游戏', '当前展示 {games.length} / {catalog.data?.total || 0} 款目录游戏')
card = "apps/web/components/game-card.tsx"
replace(card, 'import { safeExternalUrl } from "@/lib/api";', 'import { safeExternalUrl } from "@/lib/api";\nimport { metadataLabel } from "@/lib/retrieval-labels";')
replace(card, '<Gamepad2 size={12} /> PC', '<Gamepad2 size={12} /> {game.platforms.join(" / ") || "平台待核验"}')
replace(card, '<div className="card-eyebrow">{game.genres.slice(0, 2).join(" · ") || "探索游戏"}</div>', '<div className="card-eyebrow">{game.genres.slice(0, 2).join(" · ") || "探索游戏"}</div>\n        <p className="fine-print">{metadataLabel(game.metadata_status)}</p>')
replace("compose.yaml", '      RUN_WORKERS: 2', '''      RETRIEVAL_MODE: ${RETRIEVAL_MODE:-bm25}
      EMBEDDING_BASE_URL: ${EMBEDDING_BASE_URL:-}
      EMBEDDING_MODEL: ${EMBEDDING_MODEL:-}
      EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-}
      EMBEDDING_TIMEOUT_SECONDS: ${EMBEDDING_TIMEOUT_SECONDS:-8}
      SEMANTIC_MIN_SCORE: ${SEMANTIC_MIN_SCORE:-0.65}
      RUN_WORKERS: 2''')
# Keep primary documentation current instead of contradictory appended statements.
readme = Path("README.md")
text = readme.read_text().replace("明确使用规则与关键词检索", "明确使用规则解析与 BM25 词项检索")
text = text.replace("按需预算报价、解释；规则基线与可配置模型路径", "按需预算报价、解释；BM25 默认、可选向量融合与模型解析")
text = text.replace("IGDB 的 client credentials 适配器已实现，尚未自动导入目录；", "IGDB 的 client credentials 适配器已实现，新增审核后规范化文件的事务性导入；尚未自动同步。")
text = text.replace("Redis/Celery 分布式投递、跨进程恢复和向量检索尚未启用。", "Redis/Celery 分布式投递和跨进程恢复尚未启用。向量检索可显式配置，实际质量与线上可用性仍需验证。")
text = text.replace("推进目录导入、混合检索、持久队列与 checkpoint 恢复、更大规模评估", "推进真实 IGDB 同步、更大目录与语义质量评估、持久队列与 checkpoint 恢复")
readme.write_text(text)
path = Path("docs/implementation-status.md")
path.write_text(path.read_text() + '''

## v0.2 recovery increment

Implemented: BM25 default and preserved lexical baseline; opt-in bounded embedding/RRF
with explicit degradation; atomic operator-reviewed normalized IGDB import, receipts,
curated preservation and stale/conflicting identity rejection; UI page navigation,
per-record metadata source and actual retrieval/fingerprint display; Docker option
passthrough. Original v0.1 sections above are historical, not the current capability list.

The original interrupted v0.2 was not recovered. Do not reuse its reported 132 Python /
11 web / 7 browser counts or model results. This increment has its own CI evidence.
No bulk live IGDB import, independent semantic model evaluation, notifications, distributed
execution or production readiness is claimed. Main changes only after final acceptance.
''')
Path(__file__).unlink()
print("UI pagination, source labels, retrieval status and deployment configuration aligned.")
