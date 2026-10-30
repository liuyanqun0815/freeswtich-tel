"""FreeSWITCH mod_xml_curl XML builders."""

from __future__ import annotations

import hashlib
from xml.sax.saxutils import escape


def a1_hash(user: str, domain: str, password: str) -> str:
    return hashlib.md5(f"{user}:{domain}:{password}".encode()).hexdigest()


def not_found_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="result">\n'
        '    <result status="not found"/>\n'
        "  </section>\n"
        "</document>\n"
    )


def directory_xml(domain: str, user: str, a1: str, agent_id: str) -> str:
    domain_e = escape(domain)
    user_e = escape(user)
    a1_e = escape(a1)
    agent_e = escape(agent_id)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="directory">\n'
        f'    <domain name="{domain_e}">\n'
        f'      <user id="{user_e}">\n'
        "        <params>\n"
        f'          <param name="a1-hash" value="{a1_e}"/>\n'
        '          <param name="cacheable" value="true"/>\n'
        "        </params>\n"
        "        <variables>\n"
        f'          <variable name="agent_id" value="{agent_e}"/>\n'
        "        </variables>\n"
        "      </user>\n"
        "    </domain>\n"
        "  </section>\n"
        "</document>\n"
    )


def park_dialplan(tenant: str, entry: str, park_timeout: int) -> str:
    tenant_e = escape(tenant)
    entry_e = escape(entry)
    timeout_e = escape(str(park_timeout))
    extension = (
        "      <extension name=\"vr_unified_park\">\n"
        "        <condition field=\"destination_number\" expression=\"^(.*)$\">\n"
        "          <action application=\"export\" data=\"vr_call_id=${uuid}\"/>\n"
        f"          <action application=\"export\" data=\"vr_tenant={tenant_e}\"/>\n"
        f"          <action application=\"export\" data=\"vr_entry={entry_e}\"/>\n"
        f"          <action application=\"export\" data=\"park_timeout={timeout_e}\"/>\n"
        "          <action application=\"park\"/>\n"
        "        </condition>\n"
        "      </extension>\n"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="dialplan">\n'
        '    <context name="public">\n'
        f"{extension}"
        "    </context>\n"
        '    <context name="default">\n'
        f"{extension}"
        "    </context>\n"
        "  </section>\n"
        "</document>\n"
    )


def _gateway_param_lines(gateway: dict) -> list[str]:
    lines: list[str] = []
    mapping = [
        ("username", "username"),
        ("password", "password"),
        ("realm", "realm"),
        ("proxy", "proxy"),
        ("register", "register"),
    ]
    for key, param_name in mapping:
        if key not in gateway or gateway[key] is None:
            continue
        value = gateway[key]
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(
            f'          <param name="{escape(param_name)}" value="{escape(str(value))}"/>'
        )
    return lines


def acl_xml(cidrs: list[str]) -> str:
    nodes: list[str] = []
    for raw in cidrs:
        cidr = str(raw).strip()
        if not cidr:
            continue
        if "/" not in cidr:
            cidr = f"{cidr}/32"
        nodes.append(
            f'          <node type="allow" cidr="{escape(cidr)}"/>'
        )
    inner = "\n".join(nodes)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="configuration">\n'
        '    <configuration name="acl.conf" description="Network Lists">\n'
        '      <network-lists>\n'
        '        <list name="vr_acl" default="deny">\n'
        f"{inner}\n"
        "        </list>\n"
        "      </network-lists>\n"
        "    </configuration>\n"
        "  </section>\n"
        "</document>\n"
    )


def _profile_settings_xml(params: list[tuple[str, str]]) -> str:
    lines = [f'          <param name="{escape(name)}" value="{escape(value)}"/>' for name, value in params]
    return "        <settings>\n" + "\n".join(lines) + "\n        </settings>"


def _gateways_xml(gateways: list[dict]) -> str:
    blocks: list[str] = []
    for gateway in gateways:
        name = escape(str(gateway.get("name", "")))
        params = "\n".join(_gateway_param_lines(gateway))
        blocks.append(f'          <gateway name="{name}">\n{params}\n          </gateway>')
    inner = "\n".join(blocks)
    return f"        <gateways>\n{inner}\n        </gateways>"


# Minimal settings so xml_curl can replace the whole sofia.conf without "No Settings".
_INTERNAL_SETTINGS = [
    ("context", "public"),
    ("dialplan", "XML"),
    ("sip-ip", "$${local_ip_v4}"),
    ("rtp-ip", "$${local_ip_v4}"),
    ("sip-port", "5060"),
    ("ws-binding", ":5066"),
    ("wss-binding", ":7443"),
    ("tls", "true"),
    ("tls-only", "false"),
    ("tls-bind-params", "transport=tls"),
    ("tls-sip-port", "5061"),
    ("tls-cert-dir", "$${certs_dir}"),
    ("tls-version", "tlsv1.2"),
    ("inbound-codec-prefs", "OPUS,PCMU,PCMA"),
    ("outbound-codec-prefs", "OPUS,PCMU,PCMA"),
    ("apply-nat-acl", "nat.auto"),
    ("local-network-acl", "localnet.auto"),
    ("manage-presence", "false"),
]

_EXTERNAL_SETTINGS = [
    ("context", "public"),
    ("dialplan", "XML"),
    ("sip-ip", "$${local_ip_v4}"),
    ("rtp-ip", "$${local_ip_v4}"),
    ("sip-port", "5080"),
    ("tls", "false"),
    ("inbound-codec-prefs", "PCMU,PCMA"),
    ("outbound-codec-prefs", "PCMU,PCMA"),
    ("manage-presence", "false"),
]


def gateway_xml(gateways: list[dict]) -> str:
    """Build a complete sofia.conf: profile settings + published gateways.

    mod_xml_curl replaces the entire sofia.conf document. Returning only
    ``<gateways>`` leaves profiles without ``<settings>`` and FreeSWITCH logs
    ``No Settings``, which takes down WSS (:7443) registration.
    """
    by_profile: dict[str, list[dict]] = {"internal": [], "external": []}
    for gateway in gateways:
        profile = str(gateway.get("profile") or "external")
        by_profile.setdefault(profile, []).append(gateway)

    profile_blocks: list[str] = []
    for profile in sorted(by_profile):
        items = by_profile[profile]
        if profile == "internal":
            settings = _profile_settings_xml(_INTERNAL_SETTINGS)
        elif profile == "external":
            settings = _profile_settings_xml(_EXTERNAL_SETTINGS)
        else:
            # Unknown profiles still need a settings node or sofia rejects them.
            settings = _profile_settings_xml(
                [
                    ("context", "public"),
                    ("dialplan", "XML"),
                    ("sip-ip", "$${local_ip_v4}"),
                    ("rtp-ip", "$${local_ip_v4}"),
                    ("sip-port", "5090"),
                ]
            )
        profile_blocks.append(
            f'      <profile name="{escape(profile)}">\n'
            f"{settings}\n"
            f"{_gateways_xml(items)}\n"
            "      </profile>"
        )

    profiles_inner = "\n".join(profile_blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="configuration">\n'
        '    <configuration name="sofia.conf" description="SIP Endpoint">\n'
        "      <global_settings>\n"
        '        <param name="log-level" value="0"/>\n'
        '        <param name="debug-presence" value="0"/>\n'
        "      </global_settings>\n"
        "      <profiles>\n"
        f"{profiles_inner}\n"
        "      </profiles>\n"
        "    </configuration>\n"
        "  </section>\n"
        "</document>\n"
    )
