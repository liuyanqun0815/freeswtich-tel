import { sipAor, sipProfileFromLogin, sipTarget } from "./sipProfile";

test("login without an extension does not create a SIP profile", () => {
  expect(
    sipProfileFromLogin({
      extension: null,
      sip_password: null,
      domain: null,
      wss_url: null,
    }),
  ).toBeNull();
});

test("login with a bound extension keeps the temporary SIP password", () => {
  const profile = sipProfileFromLogin({
    extension: "1000",
    sip_password: "temp-secret",
    domain: "t001.vr.local",
    wss_url: "wss://fs.example:7443",
    ice_servers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  expect(profile).toEqual({
    extension: "1000",
    password: "temp-secret",
    domain: "t001.vr.local",
    wssUrl: "wss://fs.example:7443",
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  expect(profile && sipAor(profile)).toBe("sip:1000@t001.vr.local");
  expect(profile && sipTarget(profile, "1001")).toBe("sip:1001@t001.vr.local");
});
