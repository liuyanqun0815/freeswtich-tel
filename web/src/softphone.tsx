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
  trace: string;
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
  trace: "",
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

type ToneKind = "ring" | "ringback";

function toneIsOn(kind: ToneKind, elapsed: number): boolean {
  const cycle = elapsed % 5;
  if (kind === "ringback") {
    return cycle < 1;
  }
  return cycle < 0.4 || (cycle >= 0.6 && cycle < 1);
}

function startTone(kind: ToneKind, context: AudioContext): () => void {
  const osc = context.createOscillator();
  const gain = context.createGain();
  osc.frequency.value = 450;
  gain.gain.value = 0;
  osc.connect(gain);
  gain.connect(context.destination);
  osc.start();
  const started = context.currentTime;
  const timer = window.setInterval(() => {
    const on = toneIsOn(kind, context.currentTime - started);
    gain.gain.setTargetAtTime(on ? 0.12 : 0, context.currentTime, 0.015);
  }, 50);
  return () => {
    window.clearInterval(timer);
    try {
      osc.stop();
    } catch {
      // Already stopped.
    }
    osc.disconnect();
    gain.disconnect();
  };
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
  const lastWarn = useRef("");
  const sawRegistered = useRef(false);
  const [status, setStatus] = useState<SoftphoneStatus>("idle");
  const [error, setError] = useState("");
  const [trace, setTrace] = useState("");
  const [extension, setExtension] = useState("");
  const [incoming, setIncoming] = useState("");
  const [callState, setCallState] = useState<CallState>("idle");
  const toneContext = useRef<AudioContext | null>(null);
  const toneKind: ToneKind | "" = incoming ? "ring" : callState === "calling" ? "ringback" : "";

  useEffect(() => {
    const unlock = () => {
      if (!toneContext.current) {
        toneContext.current = new AudioContext();
      }
      void toneContext.current.resume();
    };
    window.addEventListener("pointerdown", unlock);
    return () => window.removeEventListener("pointerdown", unlock);
  }, []);

  useEffect(() => {
    if (!toneKind) {
      return;
    }
    if (!toneContext.current) {
      toneContext.current = new AudioContext();
    }
    const context = toneContext.current;
    void context.resume();
    return startTone(toneKind, context);
  }, [toneKind]);

  const note = useCallback((message: string) => {
    const line = `${new Date().toLocaleTimeString()} ${message}`;
    console.info("[softphone]", message);
    setTrace((prev) => {
      const lines = prev ? prev.split("\n") : [];
      lines.push(line);
      return lines.slice(-8).join("\n");
    });
  }, []);

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
      note(
        profile
          ? "未开始注册：音频元件尚未就绪"
          : "未开始注册：登录响应里没有分机或 SIP 密码",
      );
      profileRef.current = null;
      setStatus("idle");
      setError("");
      setExtension("");
      setIncoming("");
      setCallState("idle");
      return;
    }
    profileRef.current = profile;
    sawRegistered.current = false;
    lastWarn.current = "";
    setExtension(profile.extension);
    setStatus("registering");
    setError("");
    note(`开始注册 ${profile.extension}@${profile.domain} -> ${profile.wssUrl}`);
    const user = new Web.SimpleUser(profile.wssUrl, {
      aor: sipAor(profile),
      media: { remote: { audio: audioRef.current } },
      userAgentOptions: {
        authorizationUsername: profile.extension,
        authorizationPassword: profile.password,
        displayName: profile.extension,
        logLevel: "log",
        logConfiguration: false,
        logConnector: (_level, _category, _label, content) => {
          if (_level !== "warn" && _level !== "error") {
            return;
          }
          const text = String(content).split("\n")[0].slice(0, 300);
          lastWarn.current = text;
          console.warn("[sip]", text);
          if (mine === generation.current) {
            setError(text);
          }
        },
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
        note(`SIP 注册成功 ${profile.extension}@${profile.domain}`);
        sawRegistered.current = true;
        setStatus("registered");
        setError("");
      },
      onUnregistered: () => {
        if (mine !== generation.current) {
          return;
        }
        const reason = lastWarn.current;
        note(reason ? `SIP 未注册：${reason}` : "SIP 未注册，没有收到 200");
        if (!sawRegistered.current) {
          setStatus("failed");
          setError(reason || "注册未成功，FreeSWITCH 没有返回 200");
          return;
        }
        setStatus((current) => (current === "failed" ? current : "idle"));
      },
      onServerDisconnect: (err) => {
        if (mine !== generation.current) {
          return;
        }
        const text = err ? errorText(err) : "与 FreeSWITCH 的 WebSocket 已断开";
        note(text);
        setStatus("failed");
        setError(text);
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
        const caller = callerOf(user);
        note(`收到来电 ${caller}`);
        setIncoming(caller);
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
        note("通话结束");
        setIncoming("");
        setCallState("idle");
      },
    };
    userRef.current = user;
    try {
      note(`WebSocket 连接中 ${profile.wssUrl}`);
      await user.connect();
      if (mine !== generation.current) {
        await stopUser(user);
        return;
      }
      note("WebSocket 已连接，正在发送 REGISTER");
      await user.register();
      if (mine === generation.current) {
        note("REGISTER 已发出，等待 FreeSWITCH 响应");
      }
    } catch (err) {
      const text = errorText(err);
      note(text);
      if (mine !== generation.current) {
        return;
      }
      setStatus("failed");
      setError(text);
      userRef.current = null;
      await stopUser(user);
    }
  }, [note, stopUser]);

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
      trace,
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
        note(`呼叫 ${sipTarget(profile, number)}`);
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
        setTrace("");
        setExtension("");
        setIncoming("");
        setCallState("idle");
        await stopUser(user);
      },
    }),
    [status, error, trace, extension, incoming, callState, stopUser],
  );

  return (
    <SoftphoneContext.Provider value={api}>
      <audio ref={audioRef} autoPlay />
      {children}
    </SoftphoneContext.Provider>
  );
}
