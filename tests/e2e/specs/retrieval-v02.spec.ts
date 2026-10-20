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
