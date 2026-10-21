import { expect, test } from '@playwright/test';

test('real disabled synchronization and empty discovery do not imply live coverage', async ({ page }) => {
  await page.goto('/');
  const section = page.getByRole('region', { name: 'Steam 自动同步目录' });
  await expect(section.getByText('同步未启用', { exact: true })).toBeVisible();
  await expect(section.getByText('已发现 0 款', { exact: true })).toBeVisible();
  await section.getByRole('button', { name: '浏览 Steam 发现目录' }).click();
  await expect(section.getByText('没有匹配的已同步记录。首次同步前，此处为空。')).toBeVisible();
  await expect(page.getByRole('region', { name: '目录覆盖与证据范围' })).toContainText('48 款');
});

test('synthetic directory keyset contract exposes pending metadata, not fictional details', async ({ page }) => {
  const requests: string[] = [];
  await page.route('**/api/v1/catalog/steam/games?*', async route => {
    const url = new URL(route.request().url());
    requests.push(url.search);
    const next = url.searchParams.get('after_appid') === '24';
    await route.fulfill({ json: {
      items: [{ appid: next ? 25 : 24, name: next ? 'Synthetic fixture B' : 'Synthetic fixture A',
        store_url: 'https://store.steampowered.com/app/24/', observed_at: 0, source_modified: null,
        metadata_status: 'pending', game_id: null }],
      next_appid: next ? null : 24, search_mode: 'name_prefix_or_exact_appid',
    } });
  });
  await page.goto('/');
  const section = page.getByRole('region', { name: 'Steam 自动同步目录' });
  await section.getByRole('button', { name: '浏览 Steam 发现目录' }).click();
  await expect(section.getByText('Synthetic fixture A')).toBeVisible();
  await section.getByRole('button', { name: '下一批', exact: true }).click();
  await expect(section.getByText('Synthetic fixture B')).toBeVisible();
  await expect(section.getByRole('link', { name: '官方商店' })).toHaveAttribute('href', 'https://store.steampowered.com/app/25/');
  await section.getByLabel('游戏名称前缀或精确 AppID').fill('A&B');
  await section.getByRole('button', { name: '搜索 Steam 目录' }).click();
  await expect(section.getByText('Synthetic fixture A')).toBeVisible();
  expect(requests.some(query => query.includes('q=A%26B') && query.includes('after_appid=0'))).toBeTruthy();
});

test('synthetic status failure can retry real read without triggering synchronization', async ({ page }) => {
  let unavailable = true;
  await page.route('**/api/v1/catalog/steam/status', async route => {
    if (unavailable) await route.fulfill({ status: 503, json: { detail: 'Synthetic unavailable' } });
    else await route.continue();
  });
  await page.goto('/');
  const section = page.getByRole('region', { name: 'Steam 自动同步目录' });
  await expect(section.getByRole('alert')).toContainText('同步状态暂时不可用');
  unavailable = false;
  await section.getByRole('button', { name: '刷新同步状态' }).click();
  await expect(section.getByText('同步未启用', { exact: true })).toBeVisible();
});
