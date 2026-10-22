import { useEffect, useState } from "react";
import { api, getToken } from "./auth";

export type PhoneStatus = {
  extension: string;
  domain: string;
  wssUrl: string;
  registered: boolean;
  contact: string;
  ready: boolean;
};

const EMPTY: PhoneStatus = {
  extension: "",
  domain: "",
  wssUrl: "",
  registered: false,
  contact: "",
  ready: false,
};

export function usePhoneStatus(): PhoneStatus {
  const [status, setStatus] = useState<PhoneStatus>(EMPTY);

  useEffect(() => {
    let stop = false;

    async function pull() {
      if (!getToken()) {
        if (!stop) {
          setStatus({ ...EMPTY, ready: true });
        }
        return;
      }
      try {
        const resp = await api("/auth/me");
        if (!resp.ok || stop) {
          return;
        }
        const data = (await resp.json()) as {
          extension?: string | null;
          domain?: string | null;
          wss_url?: string | null;
          registered?: boolean;
          contact?: string | null;
        };
        if (!stop) {
          setStatus({
            extension: data.extension ?? "",
            domain: data.domain ?? "",
            wssUrl: data.wss_url ?? "",
            registered: Boolean(data.registered),
            contact: data.contact ?? "",
            ready: true,
          });
        }
      } catch {
        return;
      }
    }

    void pull();
    const timer = window.setInterval(() => void pull(), 3000);
    const onAuth = () => void pull();
    window.addEventListener("vr-auth", onAuth);
    return () => {
      stop = true;
      window.clearInterval(timer);
      window.removeEventListener("vr-auth", onAuth);
    };
  }, []);

  return status;
}
