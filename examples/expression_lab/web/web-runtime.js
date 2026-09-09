(function exposeExpressionLabWeb(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ExpressionLabWeb = api;
}(typeof globalThis === "object" ? globalThis : this, () => {
  "use strict";

  function responseErrorDetail(payload, status) {
    const detail = payload?.detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => item?.msg || String(item)).join("；");
    }
    return typeof detail === "string" && detail.trim()
      ? detail.trim()
      : `请求失败（HTTP ${status}）`;
  }

  async function decodeApiResponse(response) {
    const contentType = response.headers?.get("content-type") || "";
    const rawBody = (await response.text()).trim();
    let payload = {};
    if (contentType.includes("application/json") && rawBody) {
      try {
        payload = JSON.parse(rawBody);
      } catch (_) {
        if (response.ok) throw new Error("SDK 返回了无效的 JSON 响应");
        payload = { detail: rawBody };
      }
    } else if (rawBody) {
      payload = { detail: rawBody };
    }
    if (!response.ok) throw new Error(responseErrorDetail(payload, response.status));
    return payload;
  }

  async function fetchWithTimeout(fetchImplementation, url, options = {}, timeoutMs = 6000) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetchImplementation(url, { ...options, signal: controller.signal });
    } catch (error) {
      if (controller.signal.aborted) throw new Error("SDK 响应超时，请检查 Application 是否仍在运行");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  return { decodeApiResponse, fetchWithTimeout };
}));

