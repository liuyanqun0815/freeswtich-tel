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


def gateway_xml(gateways: list[dict]) -> str:
    by_profile: dict[str, list[dict]] = {}
    for gateway in gateways:
        profile = str(gateway.get("profile") or "external")
        by_profile.setdefault(profile, []).append(gateway)
    if not by_profile:
        by_profile = {"external": []}

    profile_blocks: list[str] = []
    for profile, items in sorted(by_profile.items()):
        gateway_blocks: list[str] = []
        for gateway in items:
            name = escape(str(gateway.get("name", "")))
            params = "\n".join(_gateway_param_lines(gateway))
            gateway_blocks.append(
                f'          <gateway name="{name}">\n{params}\n          </gateway>'
            )
        gateways_inner = "\n".join(gateway_blocks) if gateway_blocks else ""
        profile_blocks.append(
            f'        <profile name="{escape(profile)}">\n'
            f"          <gateways>\n{gateways_inner}\n          </gateways>\n"
            "        </profile>"
        )

    profiles_inner = "\n".join(profile_blocks)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<document type="freeswitch/xml">\n'
        '  <section name="configuration">\n'
        '    <configuration name="sofia.conf" description="SIP Endpoint">\n'
        "      <profiles>\n"
        f"{profiles_inner}\n"
        "      </profiles>\n"
        "    </configuration>\n"
        "  </section>\n"
        "</document>\n"
    )
