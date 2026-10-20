"use client";

export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="main-container">
      <section className="empty-state">
        <h1>这一页暂时迷路了</h1>
        <p>页面遇到了意外问题。可以重新加载，再继续寻找你的下一款游戏。</p>
        <button className="button button-primary" onClick={reset}>
          重新加载
        </button>
      </section>
    </main>
  );
}
