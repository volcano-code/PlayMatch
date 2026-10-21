import { expect, test } from '@playwright/test';

// Synthetic list-response tests are UI contracts, not live catalog coverage claims.
test('catalog pagination, provider labels and filter reset use explicit server pages', async ({ page, request }) => {
  const body = await (await request.get('/api/v1/games?limit=1')).json();
  const base = body.items[0];
  const items = Array.from({ length: 25 }, (_, i) => ({
    ...base, id: `synthetic-page-${i}`, title: `Synthetic ${i}`, title_zh: `合成分页样例 ${i}`,
    metadata_status: 'provider', chinese_support: 'unknown', coop: 'unknown', horror: 'unknown',
  }));
  await page.route('**/api/v1/capabilities', async route => {
    const response = await route.fetch();
    await route.fulfill({ json: { ...await response.json(), catalog_mode: 'mixed' } });
  });
  const offsets: number[] = [];
  await page.route('**/api/v1/games?*', async route => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset') || 0);
    offsets.push(offset);
    const available = url.searchParams.has('q') ? items.slice(0, 2) : items;
    await route.fulfill({ json: { items: available.slice(offset, offset + 24), total: available.length } });
  });
  await page.goto('/');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await expect(page.getByText('含供应商导入的目录', { exact: true })).toBeVisible();
  await expect(page.getByTestId('game-card').first()).toContainText('供应商资料');
  await expect(page.getByRole('button', { name: '上一页', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByTestId('game-card')).toHaveCount(1);
  await expect(page.getByTestId('game-card')).toContainText('合成分页样例 24');
  await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeDisabled();
  await page.getByRole('group', { name: '目录筛选' }).getByRole('button', { name: '探索冒险' }).click();
  await expect(page.getByTestId('game-card')).toHaveCount(2);
  expect(offsets).toContain(24);
  expect(offsets.at(-1)).toBe(0);
});

test('a failed catalog page does not display stale prior-page cards', async ({ page, request }) => {
  const body = await (await request.get('/api/v1/games?limit=1')).json();
  let fail = true;
  await page.route('**/api/v1/games?*', async route => {
    const url = new URL(route.request().url());
    const offset = Number(url.searchParams.get('offset') || 0);
    if (offset === 24 && fail) {
      await route.fulfill({ status: 503, json: { detail: 'Synthetic page failure' } });
    } else {
      await route.fulfill({ json: {
        items: offset === 0 ? Array.from({ length: 24 }, (_, i) => ({ ...body.items[0], id: `synthetic-${i}` })) : [{ ...body.items[0], id: 'synthetic-last' }],
        total: 25,
      } });
    }
  });
  await page.goto('/');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByRole('heading', { name: '暂时无法打开游戏目录' })).toBeVisible();
  await expect(page.getByTestId('game-card')).toHaveCount(0);
  fail = false;
  await page.getByRole('button', { name: '重新连接', exact: true }).click();
  await expect(page.getByTestId('game-card')).toHaveCount(1);
});

test('real recommendation reports the actual retrieval mode and text version', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('game-card')).toHaveCount(24);
  await page.getByLabel('描述你想玩的游戏', { exact: true }).fill('想探索太空和科幻世界');
  await page.getByRole('button', { name: '为我推荐', exact: true }).click();
  await expect(page.getByRole('button', { name: '回到游戏目录' })).toBeVisible();
  const status = page.getByTestId('retrieval-status');
  await expect(status).toContainText('本次检索：BM25 词项检索');
  await expect(status).toContainText(/检索文本版本：[a-f0-9]{12}/);
  await expect(status).toContainText('排序分数不是匹配概率');
});

test('index metadata readiness and failed refresh are shown without a false service claim', async ({ page }) => {
  // UI contract fixture only. Real index storage/API semantics are in the backend suite.
  await page.route('**/api/v1/capabilities', async route => {
    const upstream = await route.fetch();
    const data = await upstream.json();
    await route.fulfill({ json: { ...data, retrieval_mode: 'indexed_hybrid', embedding_configured: true } });
  });
  let failed = false;
  await page.route('**/api/v1/retrieval/index', async route => {
    if (failed) return route.fulfill({ status: 503, json: { detail: 'Synthetic unavailable status' } });
    await route.fulfill({ json: {
      status: 'stale', active_index_id: 'synthetic-test-index', generation: 3,
      document_count: 24, dimensions: 384, catalog_fingerprint: 'b'.repeat(64),
      search_method: 'exact_cosine', max_documents: 5000, provider_probed: false,
    } });
  });
  await page.goto('/');
  const status = page.getByTestId('index-status');
  await expect(status.getByText('目录已变更，需要重建索引')).toBeVisible();
  await expect(status.getByText(/索引文档 24 条/)).toBeVisible();
  failed = true;
  await status.getByRole('button', { name: '刷新索引状态' }).click();
  await expect(status.getByText('索引状态读取失败，不能据此认定服务可用。')).toBeVisible();
});

test('refresh reconnects to the existing completed task without another recommendation POST', async ({page}) => {
  let created = 0;
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/api/v1/runs')) created++; });
  await page.goto('/');
  await page.getByLabel('描述你想玩的游戏', {exact:true}).fill('想探索太空和科幻世界');
  await page.getByRole('button', {name:'为我推荐', exact:true}).click();
  await expect(page.getByRole('button', {name:'回到游戏目录'})).toBeVisible();
  await expect(page.getByText(/任务状态：completed/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', {name:'继续查看原任务', exact:true}).click();
  await expect(page.getByText(/任务状态：completed/)).toBeVisible();
  expect(created).toBe(1);
});

test('cancel of a queued task is explicit and does not claim completed recommendations (synthetic API)', async ({page}) => {
  const id = '12345678-1234-1234-1234-123456789012';
  await page.addInitScript(() => sessionStorage.setItem('playmatch.active-run.v1', JSON.stringify({
    id:'12345678-1234-1234-1234-123456789012', access_token:'a'.repeat(43), owner:null, saved_at:Date.now()
  })));
  let cancelled = 0;
  await page.route(`**/api/v1/runs/${id}/cancel`, async route => {
    expect(route.request().method()).toBe('POST');
    expect(route.request().headers()['x-run-token']).toBe('a'.repeat(43));
    cancelled++;
    await route.fulfill({json:{id,status:'cancelled',query:'synthetic',intent:{},results:[],warnings:['任务已取消'],mode:'rules',created_at:'2026-10-20T00:00:00Z',completed_at:'2026-10-20T00:00:01Z',attempt_count:0,last_checkpoint:null}});
  });
  await page.goto('/');
  await page.getByRole('button', {name:'取消任务', exact:true}).click();
  await expect(page.getByText(/任务已取消 · 已尝试/)).toBeVisible();
  expect(cancelled).toBe(1);
  await expect(page.getByRole('heading', {name:'暂时没有找到满足条件的游戏'})).not.toBeVisible();
});


test('late cancellation response cannot replace a newer recommendation (synthetic cancellation only)', async ({page}) => {
  const id = '12345678-1234-1234-1234-123456789012';
  await page.addInitScript(() => sessionStorage.setItem('playmatch.active-run.v1', JSON.stringify({
    id:'12345678-1234-1234-1234-123456789012', access_token:'a'.repeat(43), owner:null, saved_at:Date.now()
  })));
  let release!: () => void;
  let entered!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { entered = resolve; });
  await page.route(`**/api/v1/runs/${id}/cancel`, async route => {
    entered();
    await gate;
    await route.fulfill({json:{id,status:'cancelled',query:'synthetic',intent:{},results:[],warnings:['old cancellation'],mode:'rules',created_at:'2026-10-20T00:00:00Z',completed_at:'2026-10-20T00:00:01Z',attempt_count:0,last_checkpoint:null}});
  });
  await page.goto('/');
  await page.getByRole('button', {name:'取消任务', exact:true}).click();
  await started;
  await page.getByLabel('描述你想玩的游戏', {exact:true}).fill('想探索太空和科幻世界');
  await page.getByRole('button', {name:'为我推荐', exact:true}).click();
  await expect(page.getByText(/任务状态：completed/)).toBeVisible();
  const response = page.waitForResponse(value => value.url().endsWith(`/runs/${id}/cancel`));
  release();
  await (await response).finished();
  await page.waitForTimeout(100); // Let the intentionally delayed response reach the React handler.
  await expect(page.getByText(/任务已取消 · 已尝试/)).not.toBeVisible();
  await expect(page.getByText(/任务状态：completed/)).toBeVisible();
  await expect(page.getByTestId('game-card').first()).toBeVisible();
});
