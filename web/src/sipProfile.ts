const SIP_KEY = "vr_sip_profile";

export type SipProfile = {
  extension: string;
  password: string;
  domain: string;
  wssUrl: string;
  iceServers: { urls: string }[];
};

export type LoginSipFields = {
  extension?: string | null;
  sip_password?: string | null;
  domain?: string | null;
  wss_url?: string | null;
  ice_servers?: { urls: string }[] | null;
};

export function sipProfileFromLogin(data: LoginSipFields): SipProfile | null {
  if (!data.extension || !data.sip_password || !data.domain || !data.wss_url) {
    return null;
  }
  return {
    extension: data.extension,
    password: data.sip_password,
    domain: data.domain,
    wssUrl: data.wss_url,
    iceServers: data.ice_servers ?? [],
  };
}

export function sipAor(profile: SipProfile): string {
  return `sip:${profile.extension}@${profile.domain}`;
}

export function sipTarget(profile: SipProfile, number: string): string {
  return `sip:${number.trim()}@${profile.domain}`;
}

export function saveSipProfile(profile: SipProfile | null): void {
  if (!profile) {
    sessionStorage.removeItem(SIP_KEY);
    return;
  }
  sessionStorage.setItem(SIP_KEY, JSON.stringify(profile));
}

export function loadSipProfile(): SipProfile | null {
  const raw = sessionStorage.getItem(SIP_KEY);
  if (!raw) {
    return null;
  }
  try {
    const data = JSON.parse(raw) as Partial<SipProfile>;
    if (!data.extension || !data.password || !data.domain || !data.wssUrl) {
      return null;
    }
    return {
      extension: data.extension,
      password: data.password,
      domain: data.domain,
      wssUrl: data.wssUrl,
      iceServers: data.iceServers ?? [],
    };
  } catch {
    return null;
  }
}
