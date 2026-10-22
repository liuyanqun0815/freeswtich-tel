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
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, headers });
}
