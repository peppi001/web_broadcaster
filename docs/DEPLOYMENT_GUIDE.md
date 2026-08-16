# Web Broadcaster v6042 deployment guide

This is the main deployment guide for Web Broadcaster v6042. The same Web Broadcaster package supports both a simple trusted-LAN installation and a public Internet installation. You do not need separate application binaries for the two modes.

Choose one of these deployment modes before editing `start.sh`:

```text
Mode A: trusted LAN only
Client on private LAN
       |
       | HTTP :15000
       v
Web Broadcaster v6042
0.0.0.0:15000

Mode B: public Internet, recommended
Internet
   |
   | HTTPS :443
   v
nginx on the same host
   |
   | HTTP over loopback only
   v
127.0.0.1:15000
   |
   v
Web Broadcaster v6042
```

The public mode is described in depth in `NGINX_PUBLIC_HTTPS.md`. This document explains both modes and the rules for switching safely between them.

## 1. Common requirements

Web Broadcaster v6042 can run on the supported Linux build targets:

- Debian 12 x86-64 / amd64;
- Raspberry Pi 5 with a 64-bit Raspberry Pi OS / Debian-based aarch64 system.

Build the package using the matching v6042 buildkit. The generated Linux package contains `start.sh`, the application runtime, HTML assets, scripts, and this documentation.

The first administrator on an empty database always requires the one-time setup token. This applies in both LAN and public Internet modes.

At first launch, the token is printed to the local server console and written to:

```text
db/.setup_token
```

After the first administrator is created successfully, the setup-token file is removed.

The Flask session signing secret is generated automatically and stored in:

```text
db/.session_secret
```

Preserve this file during upgrades. Deleting it invalidates existing browser sessions and causes a new secret to be generated at the next start.

## 2. Mode A — trusted LAN only

Use this mode when Web Broadcaster is reachable only from a trusted private network, for example a Raspberry Pi on a home LAN. nginx, a public domain name, Certbot, and a TLS certificate are not required.

### 2.1 Exact start.sh settings

Edit the `USER SETTINGS` section at the beginning of `start.sh` and use:

```bash
PORT="15000"
DEBUG_MODE="OFF"

PUBLIC_INTERNET_MODE="OFF"
BIND_HOST="0.0.0.0"
TRUSTED_HOSTS=""

HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

PROXY_COUNT="0"
```

These values mean:

- `PUBLIC_INTERNET_MODE="OFF"`: do not enable the public-Internet fail-closed policy;
- `BIND_HOST="0.0.0.0"`: listen on the machine's LAN interfaces so other LAN clients can connect;
- `TRUSTED_HOSTS=""`: no public hostname allow-list is required for this simple LAN mode;
- `HTTPS_MODE="OFF"`: Web Broadcaster serves plain HTTP;
- `PROXY_COUNT="0"`: there is no trusted reverse proxy in front of Web Broadcaster.

Start the application normally:

```bash
./start.sh
```

### 2.2 Find the Raspberry Pi or server LAN address

On the Web Broadcaster host:

```bash
hostname -I
```

or:

```bash
ip -4 addr
```

Assume the host address is `192.168.1.50`. Open Web Broadcaster from another machine on the same LAN at:

```text
http://192.168.1.50:15000
```

The exact address depends on the local network.

### 2.3 Verify the LAN listener

On the Web Broadcaster host:

```bash
ss -ltnp | grep ':15000'
```

For this mode, a listener such as the following is expected:

```text
0.0.0.0:15000
```

A local HTTP check should also work:

```bash
curl -I http://127.0.0.1:15000/login
```

Then verify access from a second machine on the same LAN using the host's private IP address.

### 2.4 Router and firewall rules for LAN mode

Do not create an Internet/NAT port-forward for TCP 15000. LAN mode is intentionally plain HTTP and must not be exposed directly to the public Internet.

If a host firewall is enabled, allow TCP 15000 only from the trusted local network if access is required from other LAN machines. The exact firewall command depends on the Linux firewall configuration and local subnet.

No inbound TCP 80 or TCP 443 rule is required for Web Broadcaster itself in this mode.

### 2.5 LAN-mode security note

Authentication, CSRF protection, password hashing, request limits, rate limiting, the one-time setup token, and the persistent random session secret remain active in LAN mode.

However, `HTTPS_MODE="OFF"` means the HTTP traffic itself is not encrypted. Usernames, passwords, cookies, and application traffic travel over the LAN without TLS transport encryption. Use this mode only on a trusted private network. Do not use it on an untrusted Wi-Fi network, guest network, public hotspot, or any network where arbitrary clients can observe or manipulate traffic.

If encrypted LAN access is required, use an HTTPS reverse proxy or Web Broadcaster's direct TLS mode instead.

## 3. Mode B — public Internet through nginx

This is the recommended mode when Web Broadcaster must be reachable from the Internet.

Requirements:

- nginx on the same Linux host as Web Broadcaster;
- a real user-controlled domain or subdomain;
- DNS pointing that hostname to the public server/router address;
- browser-trusted HTTPS, for example a free Let's Encrypt certificate managed by Certbot;
- public TCP 80 and TCP 443 reaching nginx;
- no public exposure of TCP 15000.

The exact Web Broadcaster `start.sh` settings are:

```bash
PORT="15000"
DEBUG_MODE="OFF"

PUBLIC_INTERNET_MODE="ON"
BIND_HOST="127.0.0.1"
TRUSTED_HOSTS="radio.example.com"

HTTPS_MODE="OFF"
TLS_CERT_FILE=""
TLS_KEY_FILE=""

PROXY_COUNT="1"
```

Replace `radio.example.com` with the actual public hostname.

In this mode Web Broadcaster is reachable only from the local nginx process through loopback. nginx is the only public web endpoint and performs TLS termination.

Do not use `PROXY_COUNT="1"` together with `BIND_HOST="0.0.0.0"`. The v6042 launcher deliberately rejects that unsafe combination.

For the complete nginx, DNS, router/firewall, Certbot/Let's Encrypt, Server-Sent Events, renewal, validation, upgrade, and troubleshooting procedure, continue with:

```text
docs/NGINX_PUBLIC_HTTPS.md
```

A ready-to-edit nginx virtual-host example is included at:

```text
docs/nginx_web_broadcaster.conf.example
```

## 4. Do not mix the two modes

The following combinations are intentionally different.

LAN-only mode:

```text
PUBLIC_INTERNET_MODE=OFF
BIND_HOST=0.0.0.0
HTTPS_MODE=OFF
PROXY_COUNT=0
```

Public nginx mode:

```text
PUBLIC_INTERNET_MODE=ON
BIND_HOST=127.0.0.1
HTTPS_MODE=OFF
PROXY_COUNT=1
```

Do not enable reverse-proxy trust merely because nginx happens to be installed on the system. Set `PROXY_COUNT=1` only when all browser traffic actually reaches Web Broadcaster through the single trusted local nginx reverse proxy.

Do not set `PUBLIC_INTERNET_MODE=ON` for plain HTTP without TLS or a trusted reverse proxy. v6042 refuses that configuration by design.

## 5. Switching from LAN-only to public nginx mode

1. Stop Web Broadcaster.
2. Configure DNS, nginx, and HTTPS according to `NGINX_PUBLIC_HTTPS.md`.
3. Change `start.sh` from the LAN settings to the public nginx settings.
4. Confirm `BIND_HOST="127.0.0.1"` before starting Web Broadcaster.
5. Start Web Broadcaster.
6. Confirm with `ss -ltnp | grep ':15000'` that only loopback is listening on TCP 15000.
7. Run `sudo nginx -t` before reloading nginx.
8. Verify the public HTTPS URL in a browser.
9. Confirm that TCP 15000 is not forwarded by the router and is not publicly reachable.

Preserve the existing `db/` directory and `db/.session_secret` during the change. Existing application data does not need to be recreated.

## 6. Switching from public nginx mode back to LAN-only

1. Stop Web Broadcaster.
2. Remove public router/NAT forwards if the public service is no longer required.
3. Disable or remove the nginx virtual host if it is no longer needed.
4. Change `start.sh` to the LAN-only settings.
5. Set `PROXY_COUNT="0"` before binding to `0.0.0.0`.
6. Start Web Broadcaster.
7. Verify LAN access using the private IP address and TCP port 15000.

Do not delete the Web Broadcaster database or `.session_secret` merely because the deployment mode changes.

## 7. Upgrade rules for both modes

When replacing Web Broadcaster with a newer release:

- preserve the runtime database and user data;
- preserve `db/.session_secret`;
- keep a copy of the working `start.sh` settings;
- use the new release's `start.sh` as the configuration baseline and reapply the intended deployment mode;
- run the buildkit's complete source regression suite, which is enabled by default;
- verify the listener address after the upgrade;
- in public mode, validate nginx with `sudo nginx -t` and test public HTTPS after the upgrade.

nginx and Certbot state live outside the Web Broadcaster release directory, so replacing the application package does not normally replace the certificate or nginx site configuration.

## 8. Quick verification checklist

For LAN-only mode, verify:

```text
PUBLIC_INTERNET_MODE="OFF"
BIND_HOST="0.0.0.0"
HTTPS_MODE="OFF"
PROXY_COUNT="0"
```

and confirm that the service is reachable only from the intended private network and that TCP 15000 is not port-forwarded to the Internet.

For public nginx mode, verify:

```text
PUBLIC_INTERNET_MODE="ON"
BIND_HOST="127.0.0.1"
TRUSTED_HOSTS="your.real.hostname"
HTTPS_MODE="OFF"
PROXY_COUNT="1"
```

and confirm that nginx owns public TCP 80/443, Web Broadcaster listens only on loopback TCP 15000, the browser certificate is valid, both SSE streams update normally, and `certbot renew --dry-run` succeeds.

## 9. Related files

```text
docs/DEPLOYMENT_GUIDE.md
docs/NGINX_PUBLIC_HTTPS.md
docs/nginx_web_broadcaster.conf.example
```

`DEPLOYMENT_GUIDE.md` is the entry point for choosing a mode. `NGINX_PUBLIC_HTTPS.md` is the detailed public deployment procedure. The nginx example is intended only for public reverse-proxy mode and is not required for LAN-only operation.
