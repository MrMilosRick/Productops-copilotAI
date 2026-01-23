const API_BASE = import.meta.env.VITE_API_BASE || "";

export async function apiFetch(path, { method = "GET", headers = {}, body } = {}) {
  const url = path;

  const isFormData =
    typeof FormData !== "undefined" && body instanceof FormData;

  const isPlainObject =
    body &&
    typeof body === "object" &&
    !Array.isArray(body) &&
    !isFormData;

  const finalHeaders = { ...headers };

  const init = { method, headers: finalHeaders };

  if (body !== undefined) {
    if (isFormData) {
      // IMPORTANT: do NOT set Content-Type manually for FormData
      init.body = body;
    } else if (isPlainObject) {
      finalHeaders["Content-Type"] = finalHeaders["Content-Type"] || "application/json";
      init.body = JSON.stringify(body);
    } else {
      // string / Blob / ArrayBuffer / etc.
      init.body = body;
    }
  }

  const res = await fetch(url, init);

  // Try parse JSON (default), but support text as fallback
  const ct = res.headers.get("content-type") || "";
  let data;
  if (ct.includes("application/json")) {
    data = await res.json().catch(() => null);
  } else {
    data = await res.text().catch(() => "");
  }

  if (!res.ok) {
    const msg =
      (data && data.detail && (data.detail.error || data.detail)) ||
      (data && data.error) ||
      (typeof data === "string" && data) ||
      `HTTP ${res.status}`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }

  return data;
}

export function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
