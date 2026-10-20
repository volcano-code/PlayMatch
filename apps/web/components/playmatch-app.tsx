"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronRight,
  CircleAlert,
  Columns3,
  Compass,
  Gamepad2,
  Globe2,
  Leaf,
  LoaderCircle,
  LogOut,
  Search,
  ShieldCheck,
  Sparkles,
  Telescope,
  WandSparkles,
  X,
} from "lucide-react";
import { api, ApiError, formatMoney, moneyToMinor } from "@/lib/api";
import { streamRun } from "@/lib/sse";
import type { Capabilities, Country, FeedbackKind, Game, Run, User, WatchItem } from "@/lib/types";
import { AuthDialog } from "./auth-dialog";
import { CompareDialog } from "./compare-dialog";
import { GameArtwork, GameCard } from "./game-card";
import { OfferDrawer } from "./offer-drawer";
import { RetrievalStatus } from "./retrieval-status";
import { catalogLabel } from "@/lib/retrieval-labels";

const themes = [
  { label: "太空探索", icon: Telescope, query: "想在太空里探索未知，喜欢有发现感的游戏" },
  { label: "治愈时光", icon: Leaf, query: "想玩轻松治愈的游戏，适合慢慢玩" },
  { label: "沉浸冒险", icon: Compass, query: "推荐世界观独特、探索和冒险为主的游戏" },
  { label: "一起开黑", icon: Gamepad2, query: "想和朋友一起玩在线合作游戏" },
  { label: "策略经营", icon: Columns3, query: "推荐有深度的策略和经营游戏" },
];

export function PlayMatchApp() {
  const client = useQueryClient();
  const [view, setView] = useState<"discover" | "saved">("discover");
  const [query, setQuery] = useState("");
  const [country, setCountry] = useState<Country>("US");
  const [budget, setBudget] = useState("");
  const [excludeOwned, setExcludeOwned] = useState(true);
  const [catalogFilter, setCatalogFilter] = useState("");
  const [catalogPage, setCatalogPage] = useState(0);
  const [run, setRun] = useState<Run | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string[]>([]);
  const [runError, setRunError] = useState("");
  const [authOpen, setAuthOpen] = useState(false);
  const [drawer, setDrawer] = useState<{ game: Game; country: Country } | null>(null);
  const [compareGames, setCompareGames] = useState<Game[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const searchRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLElement>(null);
  const capabilities = useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/capabilities"),
  });
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<User>("/auth/me");
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    retry: false,
    refetchOnWindowFocus: true,
  });
  const catalog = useQuery({
    queryKey: ["games", catalogFilter, catalogPage],
    queryFn: () =>
      api<{ items: Game[]; total: number }>(
        `/games?limit=24&offset=${catalogPage * 24}${catalogFilter ? `&q=${encodeURIComponent(catalogFilter)}` : ""}`,
      ),
  });
  const watchlist = useQuery({
    queryKey: ["watchlist", me.data?.id],
    enabled: Boolean(me.data),
    queryFn: () => api<{ items: WatchItem[] }>("/watchlist"),
    retry: false,
  });
  const storedFeedback = useQuery({
    queryKey: ["feedback", me.data?.id],
    enabled: Boolean(me.data),
    queryFn: () =>
      api<{ items: { game_id: string; kind: FeedbackKind; created_at: string }[] }>("/feedback"),
    retry: false,
  });
  const feedback: Record<string, FeedbackKind[]> = {};
  for (const item of storedFeedback.data?.items || [])
    feedback[item.game_id] = [...(feedback[item.game_id] || []), item.kind];
  const games = catalog.data?.items || [];
  const savedIds = new Set(watchlist.data?.items.map((item) => item.game.id) || []);
  const mode = capabilities.data?.agent_mode;
  const priorUser = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (me.isPending) return;
    const id = me.data?.id || null;
    if (priorUser.current && priorUser.current !== id) {
      abortRef.current?.abort();
      setRun(null);
      setProgress([]);
      setRunError("");
      setBusy(false);
      setDrawer(null);
      setCompareGames([]);
      setCompareOpen(false);
      setQuery("");
      client.removeQueries({ queryKey: ["watchlist"] });
      client.removeQueries({ queryKey: ["feedback"] });
    }
    priorUser.current = id;
  }, [me.data?.id, me.isPending, client]);

  function chooseTheme(value: string) {
    setQuery(value);
    setView("discover");
    searchRef.current?.focus();
  }
  function toggleCompare(game: Game) {
    if (compareGames.some((item) => item.id === game.id)) {
      setCompareGames((items) => items.filter((item) => item.id !== game.id));
      return;
    }
    if (compareGames.length >= 3) {
      setToast({ text: "最多同时比较三款游戏，先移除一款再试试。" });
      return;
    }
    setCompareGames((items) => [...items, game]);
  }
  async function saveWatch(game: Game, target: number | null, selectedCountry: Country) {
    if (!me.data) {
      setAuthOpen(true);
      throw new Error("登录后即可保存收藏与目标价。");
    }
    await api("/watchlist", {
      method: "POST",
      body: JSON.stringify({
        game_id: game.id,
        target_minor: target,
        country: selectedCountry,
        currency: selectedCountry === "US" ? "USD" : "CNY",
      }),
    });
    await client.invalidateQueries({ queryKey: ["watchlist"] });
    setToast({ text: "已保存到收藏。目标价已记录，自动监控与通知尚未启用。" });
  }
  async function toggleSave(game: Game) {
    if (!me.data) {
      setAuthOpen(true);
      return;
    }
    try {
      if (savedIds.has(game.id)) {
        await api(`/watchlist/${encodeURIComponent(game.id)}`, { method: "DELETE" });
        await client.invalidateQueries({ queryKey: ["watchlist"] });
        setToast({ text: "已从收藏移除。" });
      } else await saveWatch(game, null, country);
    } catch (error) {
      setToast({
        text: error instanceof Error ? error.message : "收藏未保存，请重试。",
        error: true,
      });
    }
  }
  async function sendFeedback(game: Game, kind: FeedbackKind) {
    if (!me.data) {
      setAuthOpen(true);
      return;
    }
    try {
      const existing = feedback[game.id]?.includes(kind);
      if (existing)
        await api(`/feedback/${encodeURIComponent(game.id)}/${kind}`, { method: "DELETE" });
      else
        await api("/feedback", {
          method: "POST",
          body: JSON.stringify({ game_id: game.id, kind }),
        });
      await client.invalidateQueries({ queryKey: ["feedback"] });
      setToast({
        text: existing
          ? "已撤销这条标记。"
          : kind === "owned"
            ? "已记下你拥有这款游戏；下次推荐可自动排除。"
            : "已保存你的喜好，下次推荐会参考这条反馈。",
      });
    } catch (error) {
      setToast({ text: error instanceof Error ? error.message : "反馈暂未保存。", error: true });
    }
  }
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      abortRef.current?.abort();
      setRun(null);
      setProgress([]);
      setRunError("");
      setBusy(false);
      setQuery("");
      setDrawer(null);
      setCompareOpen(false);
      setCompareGames([]);
      client.setQueryData(["me"], null);
      client.removeQueries({ queryKey: ["watchlist"] });
      client.removeQueries({ queryKey: ["feedback"] });
      setView("discover");
      setToast({ text: "已退出账户。" });
    } catch (error) {
      setToast({
        text: error instanceof Error ? error.message : "暂时无法退出，请重试。",
        error: true,
      });
    }
  }
  async function recommend(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || busy) return;
    let budgetMinor: number | null;
    try {
      budgetMinor = moneyToMinor(budget);
    } catch (error) {
      setRunError(error instanceof Error ? error.message : "请检查预算。");
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setRun(null);
    setRunError("");
    setProgress(["正在解析你的游玩偏好…"]);
    setView("discover");
    setTimeout(
      () => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      100,
    );
    try {
      const created = await api<{ id: string; status: string; access_token: string | null }>(
        "/runs",
        {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            query: query.trim(),
            country,
            budget_minor: budgetMinor,
            exclude_owned: excludeOwned,
          }),
          signal: controller.signal,
        },
      );
      try {
        await streamRun(
          created.id,
          created.access_token,
          (event) => {
            let data: Record<string, unknown>;
            try {
              data = JSON.parse(event.data) as Record<string, unknown>;
            } catch {
              return;
            }
            if (event.event === "progress" && typeof data.message === "string")
              setProgress((old) =>
                old.at(-1) === data.message ? old : [...old.slice(-3), data.message as string],
              );
            if (event.event === "result" && data.run) setRun(data.run as Run);
            if (event.event === "error" && typeof data.message === "string")
              setProgress((old) => [...old.slice(-3), data.message as string]);
          },
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) throw error;
        setProgress((old) => [...old.slice(-3), "进度连接中断，正在读取结果…"]);
      }
      for (let attempt = 0; attempt < 12; attempt++) {
        const latest = await api<Run>(`/runs/${encodeURIComponent(created.id)}`, {
          headers: created.access_token ? { "X-Run-Token": created.access_token } : {},
          signal: controller.signal,
        });
        setRun(latest);
        if (["completed", "partial", "failed"].includes(latest.status)) {
          if (latest.status === "failed") setRunError("这次推荐未能完成，请调整条件后重试。");
          break;
        }
        if (attempt === 11) {
          setRunError("任务仍在处理中，暂时未能取得完整结果。请稍后重试。");
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    } catch (error) {
      if (!controller.signal.aborted)
        setRunError(error instanceof Error ? error.message : "推荐暂未完成，请重试。");
    } finally {
      if (abortRef.current === controller) setBusy(false);
    }
  }
  function stopWaiting() {
    abortRef.current?.abort();
    setBusy(false);
    setRunError("已停止等待。服务端任务可能继续执行，你可以重新发起推荐。");
  }

  const recommendations = run?.results || [];
  return (
    <div className="site-shell">
      <a className="skip-link" href="#main">
        跳转到主要内容
      </a>
      <header className="site-header">
        <div className="header-inner">
          <button
            className="brand"
            onClick={() => {
              setView("discover");
              window.scrollTo({ top: 0, behavior: "smooth" });
            }}
            aria-label="PlayMatch 首页"
          >
            <span className="brand-icon">
              <Gamepad2 size={23} strokeWidth={2.3} />
            </span>
            <span>
              Play<span className="brand-mint">Match</span>
              <small>找到下一场冒险</small>
            </span>
          </button>
          <nav aria-label="主导航">
            <button
              className={view === "discover" ? "nav-item active" : "nav-item"}
              onClick={() => setView("discover")}
            >
              <Compass size={16} />
              发现游戏
            </button>
            <button
              className={view === "saved" ? "nav-item active" : "nav-item"}
              onClick={() => setView("saved")}
            >
              <Bookmark size={16} />
              我的收藏{savedIds.size > 0 && <span className="nav-count">{savedIds.size}</span>}
            </button>
          </nav>
          <div className="header-account">
            {me.data ? (
              <>
                <span className="user-email" title={me.data.email}>
                  {me.data.email.split("@")[0]}
                </span>
                <button className="icon-button" onClick={logout} aria-label="退出登录">
                  <LogOut size={17} />
                </button>
              </>
            ) : (
              <button
                className="button button-secondary login-button"
                onClick={() => setAuthOpen(true)}
              >
                登录 / 注册 <ArrowUpRight size={14} />
              </button>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="main-container">
        {view === "discover" ? (
          <>
            <section className="hero" aria-labelledby="hero-title">
              <div className="hero-orbit" aria-hidden="true" />
              <div className="hero-copy">
                <div className="hero-kicker">
                  <span /> 为你的好奇心，找到目的地
                </div>
                <h1 id="hero-title">
                  下一段冒险，
                  <br />
                  <span>从你的想象开始。</span>
                </h1>
                <p className="hero-description">
                  一片想探索的世界，一种想体验的心情。
                  <br className="desktop-break" />
                  告诉 PlayMatch，让适合你的游戏浮出水面。
                </p>
                <a className="hero-explore" href="#discover-results">
                  先逛逛游戏目录 <ArrowDown size={15} />
                </a>
              </div>
              <div className="hero-visual" aria-hidden="true">
                <div className="visual-glow" />
                <div className="orbit-label">
                  <Sparkles size={14} /> NEXT ADVENTURE
                </div>
                {games.length >= 2 ? (
                  <>
                    <div className="hero-poster poster-back">
                      <GameArtwork game={games[Math.min(2, games.length - 1)]} />
                    </div>
                    <div className="hero-poster poster-main">
                      <GameArtwork game={games[0]} />
                      <div className="poster-caption">
                        <span>WORLDS WORTH EXPLORING</span>
                        <strong>{games[0].title_zh || games[0].title}</strong>
                      </div>
                    </div>
                    <div className="hero-mini">
                      <GameArtwork game={games[1]} />
                      <span>
                        不止一种可能 <ArrowUpRight size={13} />
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="hero-placeholder">
                    <Gamepad2 size={68} strokeWidth={1} />
                    <span>
                      让下一场冒险
                      <br />
                      与你的心情相遇
                    </span>
                  </div>
                )}
                <div className="visual-coordinates">EXPLORE / DISCOVER / PLAY</div>
              </div>
            </section>

            <section className="search-workbench" aria-label="游戏推荐条件">
              <form onSubmit={recommend}>
                <div className="search-label">
                  <span>
                    <WandSparkles size={18} /> 描述你想玩的游戏
                  </span>
                  <span className="mode-pill">
                    <span className={`status-dot ${capabilities.isError ? "offline" : ""}`} />
                    {capabilities.isError
                      ? "服务未连接"
                      : capabilities.isPending
                        ? "连接服务中"
                        : mode === "llm"
                          ? "模型辅助推荐"
                          : "规则推荐模式"}
                  </span>
                </div>
                <div className="prompt-row">
                  <textarea
                    ref={searchRef}
                    aria-label="描述你想玩的游戏"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="比如：想探索一个奇妙的开放世界，偏爱太空、海洋或沉浸的故事，节奏轻松一点…"
                    maxLength={2000}
                    required
                    rows={2}
                  />
                  <button
                    className="button button-primary recommend-button"
                    type="submit"
                    disabled={busy}
                  >
                    {busy ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />}
                    <span>{busy ? "正在寻找…" : "为我推荐"}</span>
                    {!busy && <ArrowRight size={17} />}
                  </button>
                </div>
                <div className="search-options">
                  <div className="search-fields">
                    <label className="country-control">
                      <Globe2 size={15} />
                      <span className="sr-only">购买地区</span>
                      <select
                        aria-label="购买地区"
                        value={country}
                        onChange={(e) => setCountry(e.target.value as Country)}
                      >
                        <option value="US">美国 · USD 报价</option>
                        <option value="CN">中国大陆 · CNY（仅推荐）</option>
                      </select>
                    </label>
                    <label className="budget-control">
                      <span>预算上限</span>
                      <span className="currency-prefix">{country === "US" ? "$" : "¥"}</span>
                      <input
                        aria-label="预算上限"
                        inputMode="decimal"
                        value={budget}
                        onChange={(e) => setBudget(e.target.value)}
                        placeholder="不限"
                        maxLength={10}
                      />
                    </label>
                  </div>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={excludeOwned}
                      onChange={(e) => setExcludeOwned(e.target.checked)}
                    />
                    排除已拥有的游戏
                  </label>
                </div>
              </form>
              <div className="search-note">
                <ShieldCheck size={14} />
                <p>
                  {country === "CN"
                    ? "国区价格暂未接入；推荐可用，预算内价格无法核验。"
                    : "报价仅覆盖美国地区，以实际商店、版本与查询时间为准。"}
                  {mode === "rules" ? " 当前未连接生成模型，使用规则与目录检索。" : ""}
                </p>
              </div>
            </section>

            <div className="theme-strip">
              <span className="theme-intro">从一个灵感开始</span>
              {themes.map((theme) => (
                <button
                  key={theme.label}
                  onClick={() => chooseTheme(theme.query)}
                  className={query === theme.query ? "theme-chip active" : "theme-chip"}
                >
                  <theme.icon size={15} />
                  {theme.label}
                </button>
              ))}
            </div>

            <section
              className="results-section"
              id="discover-results"
              ref={resultRef}
              aria-labelledby="results-heading"
            >
              <div className="section-heading">
                <div>
                  <span className="eyebrow">
                    {run || busy ? "YOUR NEXT CHAPTER" : "A LITTLE INSPIRATION"}
                  </span>
                  <h2 id="results-heading">
                    {run || busy ? "为你寻找的下一场冒险" : "好故事，值得被你发现"}
                  </h2>
                  <p>
                    {run
                      ? `根据你的主题与条件整理 · ${run.mode === "llm" ? "模型辅助" : "规则推荐"}`
                      : "从起始目录探索，再用你的偏好缩小选择。"}
                  </p>
                </div>
                {run && !busy ? (
                  <button
                    className="text-button"
                    onClick={() => {
                      setRun(null);
                      setRunError("");
                    }}
                  >
                    回到游戏目录 <ArrowRight size={15} />
                  </button>
                ) : (
                  <span className="catalog-caption">
                    {catalogLabel(capabilities.data?.catalog_mode)}
                    <span className="tiny-dot" />
                  </span>
                )}
              </div>
              <RetrievalStatus run={run} capabilities={capabilities.data} />
              {busy && (
                <div className="progress-panel" role="status" aria-live="polite">
                  <div className="progress-orb">
                    <Sparkles size={20} />
                  </div>
                  <div className="progress-content">
                    <strong>{progress.at(-1) || "正在寻找…"}</strong>
                    <div>
                      {progress.slice(-3, -1).map((line, i) => (
                        <span key={i}>
                          <Check size={12} />
                          {line}
                        </span>
                      ))}
                    </div>
                  </div>
                  <button className="text-button muted" onClick={stopWaiting}>
                    停止等待
                  </button>
                </div>
              )}
              {runError && (
                <div className="notice error" role="alert">
                  <CircleAlert size={18} />
                  <div>
                    <strong>这次探索遇到一点阻碍</strong>
                    <p>{runError}</p>
                  </div>
                </div>
              )}
              {run?.warnings.map((warning, i) => (
                <div className="notice" key={i}>
                  <CircleAlert size={16} />
                  <p>{warning}</p>
                </div>
              ))}
              {!run && !busy && (
                <div className="catalog-tabs" role="group" aria-label="目录筛选">
                  {[
                    { label: "全部灵感", value: "" },
                    { label: "探索冒险", value: "探索" },
                    { label: "轻松治愈", value: "治愈" },
                    { label: "策略经营", value: "策略" },
                  ].map((tab) => (
                    <button
                      className={catalogFilter === tab.value ? "active" : ""}
                      key={tab.label}
                      onClick={() => {
                        setCatalogFilter(tab.value);
                        setCatalogPage(0);
                      }}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              )}
              {!run && catalog.isPending && (
                <div className="game-grid">
                  {[0, 1, 2, 3].map((index) => (
                    <div className="card-skeleton" key={index}>
                      <div />
                      <span />
                      <span />
                      <span />
                    </div>
                  ))}
                </div>
              )}
              {!run && catalog.isError && (
                <div className="empty-state">
                  <CircleAlert size={30} />
                  <h3>暂时无法打开游戏目录</h3>
                  <p>{catalog.error.message}</p>
                  <button
                    className="button button-secondary"
                    onClick={() => {
                      catalog.refetch();
                      capabilities.refetch();
                    }}
                  >
                    重新连接
                  </button>
                </div>
              )}
              {((run && !busy && recommendations.length === 0 && !runError) ||
                (!run && !catalog.isPending && !catalog.isError && games.length === 0)) && (
                <div className="empty-state">
                  <Search size={30} />
                  <h3>暂时没有找到满足条件的游戏</h3>
                  <p>试着换一个主题，或放宽预算、语言等条件。我们不会悄悄修改你的要求。</p>
                  <button
                    className="button button-secondary"
                    onClick={() => {
                      setCatalogFilter("");
                      setCatalogPage(0);
                      searchRef.current?.focus();
                    }}
                  >
                    调整我的条件
                  </button>
                </div>
              )}
              <div className="game-grid">
                {(run ? recommendations.map((item) => item.game) : games).map((game) => (
                  <GameCard
                    key={game.id}
                    game={game}
                    recommendation={recommendations.find((item) => item.game.id === game.id)}
                    saved={savedIds.has(game.id)}
                    selected={compareGames.some((item) => item.id === game.id)}
                    liked={feedback[game.id]?.includes("like") || false}
                    owned={feedback[game.id]?.includes("owned") || false}
                    onOpen={(game) => setDrawer({ game, country })}
                    onSave={toggleSave}
                    onCompare={toggleCompare}
                    onFeedback={sendFeedback}
                  />
                ))}
              </div>
              {!run && !busy && ((catalog.data?.total || 0) > 24 || catalogPage > 0) && (
                <nav className="catalog-tabs" aria-label="目录分页">
                  <button
                    className="button button-secondary"
                    disabled={catalogPage === 0 || catalog.isFetching}
                    onClick={() => setCatalogPage((page) => Math.max(0, page - 1))}
                  >
                    上一页
                  </button>
                  <span role="status" aria-live="polite">
                    第 {catalogPage + 1} 页 · 共 {catalog.data?.total ?? "待加载"} 款
                  </span>
                  <button
                    className="button button-secondary"
                    disabled={
                      catalog.isFetching ||
                      !catalog.data ||
                      (catalogPage + 1) * 24 >= catalog.data.total
                    }
                    onClick={() => setCatalogPage((page) => page + 1)}
                  >
                    下一页
                  </button>
                </nav>
              )}
              {!run && games.length > 0 && (
                <p className="catalog-footnote">
                  当前展示 {games.length} / {catalog.data?.total || 0} 款目录游戏 ·
                  目录内容与推荐结果分别标识 · 图片来自游戏资料来源
                </p>
              )}
            </section>
          </>
        ) : (
          <section className="saved-section">
            <div className="section-heading">
              <div>
                <span className="eyebrow">SAVED FOR YOUR NEXT ADVENTURE</span>
                <h1>心动的游戏，慢慢来。</h1>
                <p>你的收藏和目标价，都在这里。</p>
              </div>
              <Bookmark size={32} className="mint" />
            </div>
            <div className="notice">
              <CircleAlert size={17} />
              <p>
                当前仅保存收藏与目标价，尚未启用自动价格监控或通知。打开游戏详情可主动查询报价。
              </p>
            </div>
            {!me.data ? (
              <div className="empty-state">
                <Bookmark size={34} />
                <h3>给下一场冒险留一个位置</h3>
                <p>登录后收藏游戏、记录目标价，并让推荐记住你的喜好。</p>
                <button className="button button-primary" onClick={() => setAuthOpen(true)}>
                  登录或注册
                </button>
              </div>
            ) : watchlist.isPending ? (
              <div className="loading-block">
                <LoaderCircle className="spin" size={24} />
                <p>正在打开收藏…</p>
              </div>
            ) : watchlist.isError ? (
              <div className="empty-state">
                <CircleAlert size={30} />
                <h3>收藏暂时无法读取</h3>
                <p>{watchlist.error.message}</p>
                <button className="button button-secondary" onClick={() => watchlist.refetch()}>
                  重试
                </button>
              </div>
            ) : !watchlist.data?.items.length ? (
              <div className="empty-state">
                <Compass size={32} />
                <h3>这里还没有游戏</h3>
                <p>发现让你心动的世界，然后点击卡片上的收藏按钮。</p>
                <button className="button button-primary" onClick={() => setView("discover")}>
                  去发现游戏 <ArrowRight size={16} />
                </button>
              </div>
            ) : (
              <div className="watch-grid">
                {watchlist.data.items.map((item) => (
                  <article className="watch-card" key={item.game.id}>
                    <button
                      className="watch-art-button"
                      onClick={() =>
                        setDrawer({ game: item.game, country: item.country as Country })
                      }
                    >
                      <GameArtwork game={item.game} />
                    </button>
                    <div className="watch-content">
                      <span className="eyebrow">
                        {item.country === "US" ? "美国 · USD" : "中国大陆 · CNY"}
                      </span>
                      <h3>{item.game.title_zh || item.game.title}</h3>
                      <p className="watch-target-label">
                        {item.target_minor === null
                          ? "尚未设置目标价"
                          : `目标价 ${formatMoney(item.target_minor, item.currency)}`}
                      </p>
                      <p className="fine-print">已收藏 · 监控未启用</p>
                      <div className="watch-actions">
                        <button
                          className="text-button"
                          onClick={() =>
                            setDrawer({ game: item.game, country: item.country as Country })
                          }
                        >
                          详情与报价 <ChevronRight size={15} />
                        </button>
                        <button
                          className="icon-button"
                          onClick={() => toggleSave(item.game)}
                          aria-label={`移除收藏 ${item.game.title_zh || item.game.title}`}
                        >
                          <X size={17} />
                        </button>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        )}

        <section className="trust-section" aria-label="如何理解推荐">
          <div>
            <Compass size={21} />
            <h3>从体验出发</h3>
            <p>主题、心情和游玩方式，一起决定你的下一款游戏。</p>
          </div>
          <div>
            <ShieldCheck size={21} />
            <h3>让依据看得见</h3>
            <p>推荐理由与资料来源分开呈现，未确认的信息明确保留。</p>
          </div>
          <div>
            <Globe2 size={21} />
            <h3>为购买多想一步</h3>
            <p>地区、版本、查询时间一起核对，让每笔预算花得明白。</p>
          </div>
        </section>
        <footer className="site-footer">
          <div className="footer-brand">
            <Gamepad2 size={18} />
            <strong>PlayMatch</strong>
            <span>好游戏，与你相遇。</span>
          </div>
          <span>
            起始版本 ·{" "}
            {mode === "llm" ? "模型辅助推荐" : mode === "rules" ? "规则推荐" : "服务状态待确认"} ·
            USD 价格来源 CheapShark
          </span>
        </footer>
      </main>

      {compareGames.length > 0 && (
        <aside className="compare-tray" aria-label="游戏对比清单">
          <div className="compare-tray-label">
            <Columns3 size={19} />
            <strong>对比清单</strong>
            <span>{compareGames.length}/3</span>
          </div>
          <div className="tray-games">
            {compareGames.map((game) => (
              <button
                key={game.id}
                onClick={() => toggleCompare(game)}
                aria-label={`移出对比 ${game.title_zh || game.title}`}
              >
                <GameArtwork game={game} />
                <span>{game.title_zh || game.title}</span>
                <X size={13} />
              </button>
            ))}
          </div>
          <button
            className="button button-primary"
            onClick={() => setCompareOpen(true)}
            disabled={compareGames.length < 2}
          >
            开始对比 <ArrowRight size={15} />
          </button>
          <button
            className="icon-button tray-clear"
            onClick={() => setCompareGames([])}
            aria-label="清空对比"
          >
            <X size={17} />
          </button>
        </aside>
      )}
      {toast && (
        <div
          className={`toast ${toast.error ? "toast-error" : ""}`}
          role={toast.error ? "alert" : "status"}
        >
          {toast.error ? <CircleAlert size={18} /> : <Check size={18} />}
          <span>{toast.text}</span>
          <button
            className="icon-button small"
            onClick={() => setToast(null)}
            aria-label="关闭提示"
          >
            <X size={15} />
          </button>
        </div>
      )}
      <OfferDrawer
        key={drawer?.game.id || "closed"}
        game={drawer?.game || null}
        country={drawer?.country || country}
        recommendation={recommendations.find((item) => item.game.id === drawer?.game.id)}
        onClose={() => setDrawer(null)}
        onSave={(game, target) => saveWatch(game, target, drawer?.country || country)}
      />
      <CompareDialog
        open={compareOpen}
        games={compareGames}
        country={country}
        onClose={() => setCompareOpen(false)}
      />
      <AuthDialog
        open={authOpen}
        onClose={() => setAuthOpen(false)}
        onSuccess={() => setToast({ text: "已登录，可以收藏游戏和保存喜好了。" })}
      />
    </div>
  );
}
