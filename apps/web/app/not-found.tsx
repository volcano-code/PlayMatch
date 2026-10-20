import Link from "next/link";

export default function NotFound() {
  return (
    <main className="main-container">
      <section className="empty-state">
        <span className="eyebrow">404 / UNEXPLORED TERRITORY</span>
        <h1>这里还没有新的冒险</h1>
        <p>这个页面不存在，回到首页继续发现游戏吧。</p>
        <Link href="/" className="button button-primary">
          返回发现页
        </Link>
      </section>
    </main>
  );
}
