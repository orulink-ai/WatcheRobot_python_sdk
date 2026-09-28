export function parseHttpResponseBody(body) {
  const text = String(body ?? "").trim();
  if (!text) return { message: "操作失败" };
  try {
    const payload = JSON.parse(text);
    if (payload !== null && typeof payload === "object") return payload;
  } catch (_) {
    // Uvicorn returns plain text for an unhandled HTTP 500.
  }
  return { message: text };
}
