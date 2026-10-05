# 04 — Egress, proxies, and VPN interference

Pooled gateways and IP-rotating upstreams are sensitive to egress IP. This file covers the
failure modes where the network stack, not the model config, is what broke — including one
that reliably presents as an application-level error.

## Symptom-to-cause map

| Symptom | Likely cause | Confirm |
| --- | --- | --- |
| Tunnel reports "connected" then immediately drops; `No Network` | resolver parse failure (see below) | `journalctl -u <vpn> \| grep -i resolv` |
| Gateway logs show a proxy that isn't listening | VPN's proxy mode not up | `ss -tlnp \| grep <port>` |
| Egress IP unchanged after "connecting" | traffic not routed through tunnel (proxy mode vs full-tunnel) | `curl https://<cdn>/cdn-cgi/trace` |
| Everything fails after a network/DNS change | stale DNS or proxy env in the gateway | compare a direct request vs gateway request |

## The resolver trap (cost me real time)

A VPN client can refuse to start because **one malformed line in the system resolver config**.
The client's parser is stricter than glibc's and rejects syntax the OS happily accepts.

Classic offender — a **scoped IPv6** address carrying an interface zone:

```
# /etc/resolv.conf
nameserver 192.168.100.1     ← fine
nameserver fe80::1%wifi-int  ← link-local IPv6 WITH a zone suffix
```

`fe80::1%wifi-int` is accepted by glibc and rejected by stricter resolvers, producing
`invalid IP address syntax` and a cascade of `No Network` / connection failures. Note the
symptom lands far from the cause: the VPN looks broken, while the real fault is DNS.

Diagnosis:

```bash
cat -A /etc/resolv.conf                 # control chars and exact content
journalctl -u <vpn-service> | grep -i -E 'resolv|parse|invalid IP'
```

Check whether the scope is even needed — most hosts have no global IPv6, so the link-local
resolver address is often useless and safe to drop:

```bash
ip -6 addr show scope global    # empty ⇒ no real IPv6 ⇒ link-local DNS is noise
```

Fix, temporary:

```bash
sudo sed -i '/<link-local-with-zone>/d' /etc/resolv.conf
```

Fix, durable — stop the network manager regenerating the line:

```bash
# find the connection that owns the address
nmcli -t -f NAME,DEVICE con show --active
nmcli -t -f IP4.DNS,IP6.DNS dev show <iface>
# stop it re-adding the IPv6 resolver
sudo nmcli con mod "<CONNECTION>" ipv6.ignore-auto-dns yes
```

**Temporary fixes to auto-generated files come back.** Any DHCP renewal or reconnect
regenerates the file, so the durable fix is the network-manager setting. Warn the user about
this rather than letting them discover the regression later.

## Proxy mode vs full-tunnel — two different `off` readings

A VPN client may run in **proxy mode** (it exposes a local SOCKS/HTTP listener; nothing is
routed unless a client opts in) instead of full-tunnel (all traffic is redirected).

Two consequences that read as bugs but are not:

1. A public "am I protected?" endpoint may report the tunnel as **off** for a plain request,
   because that request didn't use the proxy. Correct behaviour. Verify with a request that
   explicitly uses the proxy port.
2. A gateway that previously relied on that proxy will start failing the moment the proxy port
   disappears, with an error like `proxy unreachable` / `connection reset`. The proxy is
   part of the gateway's egress path even though the gateway config may not mention it.

```bash
ss -tlnp | grep <proxy-port>          # is the proxy actually listening?
curl -s https://<cdn>/cdn-cgi/trace | grep -E 'warp|ip|loc'   # per-request truth
```

**Audit implicit dependencies before changing network tooling.** If a component you didn't
configure is now load-bearing — a VPN's proxy port, an env var, a resolv.conf entry — record
it, because changing it breaks something you weren't looking at.

## Diagnosing gateway egress

If a gateway's upstream calls fail, determine whether it is using the proxy you think:

1. Compare a direct request to the upstream vs one through the intended proxy. Different
   results → egress is part of the problem.
2. Check whether the gateway config has a global proxy setting, and whether a per-credential
   override is **shadowing or being shadowed by** it. Both exist in most schemas, and the
   precedence is not always intuitive; verify empirically instead of assuming.
3. Set the proxy at the narrowest useful scope (per-credential) when you only want one
   upstream to use it. A global proxy changes behaviour for every provider at once.

## IPv6: fix the cause, don't disable the symptom

If the machine has no global IPv6, link-local resolver entries are inert noise — drop them.
If it does have IPv6, disabling IPv6 wholesale to silence a resolver is too blunt: it breaks
legitimate v6 traffic and hides the real misconfiguration. Check
`ip -6 addr show scope global` first and decide from evidence.