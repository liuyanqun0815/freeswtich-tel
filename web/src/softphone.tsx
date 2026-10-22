import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Web } from "sip.js";
import { loadSipProfile, saveSipProfile, sipAor, sipTarget, type SipProfile } from "./sipProfile";

export type SoftphoneStatus = "idle" | "registering" | "registered" | "failed";

type CallState = "idle" | "calling" | "in-call";

export type SoftphoneApi = {
  status: SoftphoneStatus;
  error: string;
  extension: string;
  incoming: string;
  callState: CallState;
  call: (number: string) => Promise<void>;
  answer: () => Promise<void>;
  decline: () => Promise<void>;
  hangup: () => Promise<void>;
  stop: () => Promise<void>;
};

const IDLE: SoftphoneApi = {
  status: "idle",
  error: "",
  extension: "",
  incoming: "",
  callState: "idle",
  call: async () => undefined,
  answer: async () => undefined,
  decline: async () => undefined,
  hangup: async () => undefined,
  stop: async () => undefined,
};

const SoftphoneContext = createContext<SoftphoneApi>(IDLE);

export function useSoftphone(): SoftphoneApi {
  return useContext(SoftphoneContext);
}

function errorText(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  return "WebSocket 连接失败";
}

function callerOf(user: Web.SimpleUser): string {
  const session = (user as unknown as {
    session?: { remoteIdentity?: { uri?: { user?: string } } };
  }).session;
  return session?.remoteIdentity?.uri?.user || "未知号码";
}

export function SoftphoneProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const userRef = useRef<Web.SimpleUser | null>(null);
  const profileRef = useRef<SipProfile | null>(null);
  const generation = useRef(0);
  const [status, setStatus] = useState<SoftphoneStatus>("idle");
  const [error, setError] = useState("");
  const [extension, setExtension] = useState("");
  const [incoming, setIncoming] = useState("");
  const [callState, setCallState] = useState<CallState>("idle");

  const stopUser = useCallback(async (user: Web.SimpleUser | null) => {
    if (!user) {
      return;
    }
    try {
      await user.unregister();
    } catch {
      // The socket may already be closed.
    }
    try {
      await user.disconnect();
    } catch {
      // The socket may already be closed.
    }
  }, []);

  const start = useCallback(async () => {
    const profile = loadSipProfile();
    const mine = ++generation.current;
    const previous = userRef.current;
    userRef.current = null;
    await stopUser(previous);
    if (mine !== generation.current) {
      return;
    }
    if (!profile || !audioRef.current) {
      profileRef.current = null;
      setStatus("idle");
      setError("");
      setExtension("");
      setIncoming("");
      setCallState("idle");
      return;
    }
    profileRef.current = profile;
    setExtension(profile.extension);
    setStatus("registering");
    setError("");
    const user = new Web.SimpleUser(profile.wssUrl, {
      aor: sipAor(profile),
      media: { remote: { audio: audioRef.current } },
      userAgentOptions: {
        authorizationUsername: profile.extension,
        authorizationPassword: profile.password,
        displayName: profile.extension,
        sessionDescriptionHandlerFactoryOptions: {
          peerConnectionConfiguration: { iceServers: profile.iceServers },
        },
      },
    });
    user.delegate = {
      onRegistered: () => {
        if (mine !== generation.current) {
          return;
        }
        setStatus("registered");
        setError("");
      },
      onUnregistered: () => {
        if (mine !== generation.current) {
          return;
        }
        setStatus((current) => (current === "failed" ? current : "idle"));
      },
      onServerDisconnect: (err) => {
        if (mine !== generation.current) {
          return;
        }
        setStatus("failed");
        setError(err ? errorText(err) : "与 FreeSWITCH 的 WebSocket 已断开");
      },
      onCallCreated: () => {
        if (mine !== generation.current) {
          return;
        }
        setCallState("calling");
      },
      onCallReceived: () => {
        if (mine !== generation.current) {
          return;
        }
        setIncoming(callerOf(user));
        setCallState("calling");
      },
      onCallAnswered: () => {
        if (mine !== generation.current) {
          return;
        }
        setIncoming("");
        setCallState("in-call");
      },
      onCallHangup: () => {
        if (mine !== generation.current) {
          return;
        }
        setIncoming("");
        setCallState("idle");
      },
    };
    userRef.current = user;
    try {
      await user.connect();
      if (mine !== generation.current) {
        await stopUser(user);
        return;
      }
      await user.register();
    } catch (err) {
      if (mine !== generation.current) {
        return;
      }
      setStatus("failed");
      setError(errorText(err));
      userRef.current = null;
      await stopUser(user);
    }
  }, [stopUser]);

  useEffect(() => {
    void start();
    const onAuth = () => {
      void start();
    };
    const onHide = () => {
      const user = userRef.current;
      if (user) {
        void user.unregister();
      }
    };
    window.addEventListener("vr-auth", onAuth);
    window.addEventListener("pagehide", onHide);
    return () => {
      generation.current += 1;
      window.removeEventListener("vr-auth", onAuth);
      window.removeEventListener("pagehide", onHide);
      const user = userRef.current;
      userRef.current = null;
      void stopUser(user);
    };
  }, [start, stopUser]);

  const api = useMemo<SoftphoneApi>(
    () => ({
      status,
      error,
      extension,
      incoming,
      callState,
      call: async (number: string) => {
        const profile = profileRef.current;
        const user = userRef.current;
        if (!profile || !user) {
          throw new Error("软电话未注册");
        }
        setCallState("calling");
        await user.call(sipTarget(profile, number));
      },
      answer: async () => {
        if (!userRef.current) {
          return;
        }
        await userRef.current.answer();
      },
      decline: async () => {
        if (!userRef.current) {
          return;
        }
        await userRef.current.decline();
        setIncoming("");
      },
      hangup: async () => {
        if (!userRef.current) {
          return;
        }
        await userRef.current.hangup();
      },
      stop: async () => {
        generation.current += 1;
        const user = userRef.current;
        userRef.current = null;
        profileRef.current = null;
        saveSipProfile(null);
        setStatus("idle");
        setError("");
        setExtension("");
        setIncoming("");
        setCallState("idle");
        await stopUser(user);
      },
    }),
    [status, error, extension, incoming, callState, stopUser],
  );

  return (
    <SoftphoneContext.Provider value={api}>
      <audio ref={audioRef} autoPlay />
      {children}
    </SoftphoneContext.Provider>
  );
}
