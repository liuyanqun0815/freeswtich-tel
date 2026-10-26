import { saveSipProfile, type SipProfile } from "./sipProfile";

const TOKEN_KEY = "vr_access_token";
const USER_KEY = "vr_username";

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function getUsername(): string {
  return sessionStorage.getItem(USER_KEY) ?? "";
}

export function setSession(token: string, username: string, sip: SipProfile | null = null): void {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, username);
  saveSipProfile(sip);
  window.dispatchEvent(new Event("vr-auth"));
}

export function clearSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
  saveSipProfile(null);
  window.dispatchEvent(new Event("vr-auth"));
}

export async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (init.body && !headers.has("Content-Type") && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, headers });
}

export async function uploadMedia(file: File): Promise<{ path: string }> {
  const body = new FormData();
  body.append("file", file);
  const resp = await api("/media/upload", { method: "POST", body });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => null);
    throw new Error(typeof detail?.detail === "string" ? detail.detail : "上传失败");
  }
  return (await resp.json()) as { path: string };
}
