import { test, expect, type Page } from '@playwright/test';
import { randomUUID } from 'node:crypto';

async function openCatalog(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await expect(page.getByText('规则推荐模式', { exact: true })).toBeVisible();
}

test('a real anonymous recommendation streams results without URL credentials', async ({ page }) => {
  const pageErrors: string[] = [];
  const privateRequests: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => {
    if (request.url().includes('/api/v1/runs')) privateRequests.push(request.url());
  });
  await openCatalog(page);
  await page.getByLabel('描述你想玩的游戏', { exact: true }).fill('想探索太空和科幻世界');
  const resultResponse = page.waitForResponse(response =>
    /\/api\/v1\/runs\/[^/?]+$/.test(response.url()) && response.request().method() === 'GET'
  );
  await page.getByRole('button', { name: '为我推荐', exact: true }).click();
  const run = await (await resultResponse).json();
  expect(['completed', 'partial']).toContain(run.status);
  expect(run.mode).toBe('rules');
  expect(run.results.length).toBeGreaterThan(0);
  expect(run.results.length).toBeLessThanOrEqual(5);
  await expect(page.getByTestId('game-card')).toHaveCount(run.results.length);
  await expect(page.getByRole('button', { name: '回到游戏目录' })).toBeVisible();
  expect(privateRequests.some(url => url.endsWith('/events'))).toBeTruthy();
  expect(privateRequests.every(url => !/[?&](token|access_token|key)=/i.test(url))).toBeTruthy();
  expect(pageErrors).toEqual([]);
});

test('unknown hard constraints produce an explicit empty result', async ({ page }) => {
  await openCatalog(page);
  await page.getByLabel('描述你想玩的游戏', { exact: true }).fill('想玩轻松治愈的游戏，不要恐怖元素');
  await page.getByRole('button', { name: '为我推荐', exact: true }).click();
  await expect(page.getByRole('heading', { name: '暂时没有找到满足条件的游戏' })).toBeVisible();
  await expect(page.getByTestId('game-card')).toHaveCount(0);
  await expect(page.getByText(/无法核验是否符合不含恐怖内容/)).toBeVisible();
});

test('comparison stays limited to three games and preserves unsupported region state', async ({ page }) => {
  await openCatalog(page);
  await page.getByLabel('购买地区').selectOption('CN');
  const cards = page.getByTestId('game-card');
  for (let index = 0; index < 4; index++) {
    await cards.nth(index).getByRole('button', { name: /^加入对比/ }).click();
  }
  await expect(page.getByText('3/3', { exact: true })).toBeVisible();
  await expect(page.getByText('最多同时比较三款游戏，先移除一款再试试。')).toBeVisible();
  await page.getByRole('button', { name: '开始对比' }).click();
  const comparison = page.getByRole('dialog', { name: '好游戏之间，也可以慢慢选' });
  await expect(comparison).toBeVisible();
  await expect(comparison.locator('article')).toHaveCount(3);
  await expect(comparison.getByText('国区价格暂未接入', { exact: true })).toHaveCount(3);
  await page.keyboard.press('Escape');
  await expect(comparison).not.toBeVisible();
});

test('registration, session, target-price persistence, logout and login work through the proxy', async ({ page, context }) => {
  const email = `e2e-${randomUUID()}@example.test`;
  const password = 'PlayMatch-e2e-only-582!';
  await openCatalog(page);
  await page.getByRole('button', { name: '登录 / 注册' }).click();
  const auth = page.getByRole('dialog').filter({ has: page.getByRole('group', { name: '账户操作' }) });
  await auth.getByRole('button', { name: '注册', exact: true }).click();
  await auth.getByLabel('邮箱', { exact: true }).fill(email);
  await auth.getByLabel('密码', { exact: true }).fill(password);
  await auth.getByRole('button', { name: '创建账户', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  const session = (await context.cookies()).find(cookie => cookie.name === 'playmatch_session');
  expect(session?.httpOnly).toBeTruthy();
  expect(session?.sameSite).toBe('Lax');

  await page.getByLabel('购买地区').selectOption('CN');
  await page.getByTestId('game-card').first().getByRole('button', { name: /^查看 .* 详情与报价$/ }).click();
  const drawer = page.getByRole('dialog', { name: '游戏详情与报价' });
  await expect(drawer.getByRole('heading', { name: '暂未接入国区价格' })).toBeVisible();
  await drawer.getByLabel('目标价格').fill('19.99');
  const saved = page.waitForResponse(response => response.url().endsWith('/api/v1/watchlist') && response.request().method() === 'POST');
  await drawer.getByRole('button', { name: '保存目标价' }).click();
  const item = await (await saved).json();
  expect(item.target_minor).toBe(1999);
  expect(item.currency).toBe('CNY');
  expect(item.country).toBe('CN');
  expect(item.monitoring_status).toBe('not_enabled');
  await drawer.getByRole('button', { name: '关闭弹窗' }).click();
  await page.getByRole('button', { name: /^我的收藏/ }).click();
  await expect(page.getByText(/目标价.*19\.99/)).toBeVisible();
  await expect(page.getByText('已收藏 · 监控未启用', { exact: true })).toBeVisible();

  await page.reload();
  await page.getByRole('button', { name: /^我的收藏/ }).click();
  await expect(page.getByText(/目标价.*19\.99/)).toBeVisible();
  await page.getByRole('button', { name: '退出登录' }).click();
  await expect(page.getByRole('button', { name: '登录 / 注册' })).toBeVisible();
  expect((await context.cookies()).some(cookie => cookie.name === 'playmatch_session')).toBeFalsy();

  await page.getByRole('button', { name: '登录 / 注册' }).click();
  await auth.getByRole('button', { name: '登录', exact: true }).click();
  await auth.getByLabel('邮箱', { exact: true }).fill(email);
  await auth.getByLabel('密码', { exact: true }).fill(password);
  await auth.getByRole('button', { name: '登录账户', exact: true }).click();
  await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  await page.getByRole('button', { name: /^我的收藏/ }).click();
  await expect(page.getByText(/目标价.*19\.99/)).toBeVisible();
  await page.getByRole('button', { name: /^移除收藏/ }).click();
  await expect(page.getByRole('heading', { name: '这里还没有游戏' })).toBeVisible();
});

test('same-origin proxy preserves CSRF rejection and bounds request bodies', async ({ request }) => {
  const rejected = await request.post('/api/v1/runs', {
    headers: { Origin: 'https://untrusted.example', 'Idempotency-Key': randomUUID() },
    data: { query: '科幻探索', country: 'US' },
  });
  expect(rejected.status()).toBe(403);
  const crossSite = await request.post('/api/v1/runs', {
    headers: { 'Sec-Fetch-Site': 'cross-site', 'Idempotency-Key': randomUUID() },
    data: { query: '科幻探索', country: 'US' },
  });
  expect(crossSite.status()).toBe(403);
  const oversized = await request.post('/api/v1/runs', {
    headers: { 'Idempotency-Key': randomUUID() },
    data: { query: 'x'.repeat(70_000), country: 'US' },
  });
  expect(oversized.status()).toBe(413);
});

test('mobile layout fits the viewport and keyboard can dismiss details', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openCatalog(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByLabel('购买地区').selectOption('CN');
  await page.getByTestId('game-card').first().getByRole('button', { name: /^查看 .* 详情与报价$/ }).click();
  const drawer = page.getByRole('dialog', { name: '游戏详情与报价' });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole('heading', { name: '暂未接入国区价格' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).not.toBeVisible();
});
