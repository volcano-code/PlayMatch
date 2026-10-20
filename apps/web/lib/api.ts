export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    let message = `请求暂未完成（${response.status}），请稍后重试。`;
    if (typeof body?.detail === "string") message = body.detail;
    else if (Array.isArray(body?.detail))
      message = body.detail.map((item: { msg?: string }) => item.msg || "输入有误").join("；");
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function formatMoney(minor: number, currency = "USD") {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency,
    currencyDisplay: "code",
  }).format(minor / 100);
}

export function moneyToMinor(value: string): number | null {
  if (!value.trim()) return null;
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(value)) throw new Error("请输入有效预算，最多两位小数。");
  const [whole, fraction = ""] = value.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

export function safeExternalUrl(value: string): string | undefined {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

export function readableTime(value: string | null) {
  if (!value) return "来源未提供更新时间";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间待核验"
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}
