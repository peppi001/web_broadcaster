# Web Broadcaster public HTTPS deployment with nginx

This document describes the recommended public Internet deployment for Web Broadcaster v6042 when nginx runs on the same Linux host as Web Broadcaster and a real public domain name is available.

For the complete deployment-mode overview, including direct LAN-only operation without nginx, a domain, or TLS, read `DEPLOYMENT_GUIDE.md` first.

The target architecture is:

```text
Internet
   |
   | TCP 80 / 443
   v
nginx
   |  TLS termination / HTTPS
   |  reverse proxy
   v
127.0.0.1:15000
   |
   v
Web Broadcaster v6042
```

Web Broadcaster must not be exposed directly on TCP 15000 in this mode. nginx is the only public HTTP/HTTPS endpoint.

## 1. Requirements

You need:

- a public domain or subdomain, for example `radio.example.com`;
- DNS control for that name;
- nginx installed on the same host as Web Broadcaster;
- inbound TCP 80 and TCP 443 reaching nginx;
- Certbot with the nginx plugin;
- a built Web Broadcaster v6042 Linux package.

The examples below assume Debian 12 or Raspberry Pi OS based on Debian 12. Run commands with an account that has `sudo` access.

## 2. DNS

Create an `A` record for the public hostname and point it to the server/router public IPv4 address.

Example:

```text
radio.example.com.  A  203.0.113.10
```

If an `AAAA` record exists, it must point to a working public IPv6 address that reaches the same nginx host on TCP 80 and TCP 443. A stale or incorrect `AAAA` record can cause certificate issuance or browser access to fail even when IPv4 is correct.

Verify DNS before continuing:

```bash
dig +short A radio.example.com
dig +short AAAA radio.example.com
```

If `dig` is not installed:

```bash
getent ahosts radio.example.com
```

## 3. Router and firewall

If the nginx host is behind NAT, forward only these public TCP ports to the nginx host:

```text
TCP 80  -> nginx host TCP 80
TCP 443 -> nginx host TCP 443
```

Do not forward TCP 15000.

If a host firewall is enabled, allow inbound TCP 80 and TCP 443. The exact firewall command depends on the system. Do not open TCP 15000 because Web Broadcaster will bind to loopback only.

Let's Encrypt HTTP-01 validation requires public access to TCP 80. Keep port 80 available; the final nginx configuration redirects normal HTTP traffic to HTTPS.

## 4. Install Certbot

On Debian 12 / Raspberry Pi OS Bookworm the distribution packages are:

```bash
sudo apt update
sudo apt install certbot python3-certbot-nginx
```

Check the installation:

```bash
certbot --version
nginx -v
```

## 5. Create a temporary HTTP-only nginx site

Before a certificate exists, create a minimal HTTP-only virtual host. This site does not proxy Web Broadcaster yet; it only gives Certbot a deterministic nginx `server_name` to use for domain validation.

Create:

```bash
sudo nano /etc/nginx/sites-available/web-broadcaster
```

Use:

```nginx
server {
    listen 80;
    listen [::]:80;

    server_name radio.example.com;

    location / {
        return 404;
    }
}
```

Replace `radio.example.com` with the real public hostname.

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/web-broadcaster /etc/nginx/sites-enabled/web-broadcaster
```

If the symlink already exists, do not create another one.

Validate and reload nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 6. Obtain the free Let's Encrypt certificate

Request the certificate without asking Certbot to own the final nginx configuration:

```bash
sudo certbot certonly --nginx -d radio.example.com
```

Follow the Certbot prompts. After success, verify the certificate paths:

```bash
sudo certbot certificates
```

For a certificate named `radio.example.com`, the normal live paths are:

```text
/etc/letsencrypt/live/radio.example.com/fullchain.pem
/etc/letsencrypt/live/radio.example.com/privkey.pem
```

Do not copy the certificate or private key into the Web Broadcaster directory. nginx should read them directly from `/etc/letsencrypt`.

## 7. Install the final nginx reverse-proxy configuration

The release package contains:

```text
docs/nginx_web_broadcaster.conf.example
```

Copy it over the temporary site:

```bash
sudo cp docs/nginx_web_broadcaster.conf.example /etc/nginx/sites-available/web-broadcaster
```

Then edit it:

```bash
sudo nano /etc/nginx/sites-available/web-broadcaster
```

Replace every occurrence of:

```text
radio.example.com
```

with the real public hostname.

The final configuration intentionally has two server blocks:

- TCP 80 redirects normal HTTP traffic to HTTPS;
- TCP 443 terminates TLS and proxies to `127.0.0.1:15000`.

The two Server-Sent Events endpoints have buffering explicitly disabled:

```text
/api/console/stream
/api/ui/events
```

Web Broadcaster also sends `X-Accel-Buffering: no` for these streams, but the nginx configuration repeats the setting explicitly so the deployment contract remains obvious and independent of future proxy defaults.

Validate before reloading:

```bash
sudo nginx -t
```

Only if the validation succeeds:

```bash
sudo systemctl reload nginx
```

## 8. Configure Web Broadcaster start.sh

Edit the `USER SETTINGS` section at the beginning of the generated Web Broadcaster `start.sh`:

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

Replace `radio.example.com` with the real public hostname.

These settings are intentional:

- `PUBLIC_INTERNET_MODE="ON"` enables the fail-closed Internet policy;
- `BIND_HOST="127.0.0.1"` makes TCP 15000 reachable only from the local host;
- `TRUSTED_HOSTS` rejects unexpected Host headers;
- `HTTPS_MODE="OFF"` is correct because nginx, not Web Broadcaster, terminates TLS;
- `PROXY_COUNT="1"` tells Web Broadcaster to trust exactly one local reverse proxy for `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host`.

Do not use `PROXY_COUNT="1"` while binding Web Broadcaster to `0.0.0.0` or another externally reachable address.

## 9. Start Web Broadcaster

Start it normally from its release directory:

```bash
./start.sh
```

Verify the listener from another terminal:

```bash
ss -ltnp | grep ':15000'
```

The important result is a loopback listener such as:

```text
127.0.0.1:15000
```

It must not show:

```text
0.0.0.0:15000
```

or a public/LAN address.

A local backend check should work when the correct Host header is supplied:

```bash
curl -I -H 'Host: radio.example.com' http://127.0.0.1:15000/login
```

A deliberately wrong Host should be rejected:

```bash
curl -I -H 'Host: invalid.example' http://127.0.0.1:15000/login
```

## 10. First public access

Open:

```text
https://radio.example.com/
```

On a fresh database, Web Broadcaster creates a one-time setup token. It is printed on the local Web Broadcaster console and stored in:

```text
db/.setup_token
```

Use that token on the Setup page to create the first administrator. The setup-token file is removed after successful first-user creation.

Web Broadcaster also creates its persistent Flask session-signing secret in:

```text
db/.session_secret
```

Keep this file private and preserve it across upgrades. Deleting it invalidates existing browser sessions and causes a new secret to be generated at the next start.

## 11. Verify public HTTPS

Check the HTTP redirect:

```bash
curl -I http://radio.example.com/
```

It should redirect to HTTPS.

Check HTTPS and certificate validation:

```bash
curl -I https://radio.example.com/login
```

Do not use `curl -k` for this check; certificate verification should succeed normally.

Check nginx configuration and status:

```bash
sudo nginx -t
sudo systemctl status nginx --no-pager
```

Check that TCP 15000 remains loopback-only:

```bash
ss -ltnp | grep ':15000'
```

The public browser address must use the hostname, not the server IP address, because the TLS certificate is issued for the hostname.

## 12. Certbot automatic renewal

Certbot is designed to run `renew` periodically and only renew certificates that are close enough to expiry.

Check whether the installation already provides an automatic scheduler:

```bash
systemctl list-timers --all | grep -i certbot || true
```

On systems with `certbot.timer`, inspect it with:

```bash
sudo systemctl status certbot.timer --no-pager
```

Add a deploy hook so nginx reloads after a certificate was actually renewed:

```bash
sudo install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx >/dev/null <<'EOF_HOOK'
#!/bin/sh
systemctl reload nginx
EOF_HOOK
sudo chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
```

Test renewal without waiting for certificate expiry:

```bash
sudo certbot renew --dry-run
```

A successful dry run is an important deployment check. Do not use forced real renewals as a routine test because certificate authorities have issuance rate limits.

## 13. nginx configuration details

### Forwarded headers

The proxy must pass:

```nginx
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
```

This is required so Web Broadcaster can reconstruct the original public request correctly and apply its trusted-host, secure-cookie, HTTPS and per-client rate-limit logic behind nginx.

### Request body limit

The nginx example uses:

```nginx
client_max_body_size 4m;
```

This matches the Web Broadcaster application-level 4 MiB request limit. nginx therefore rejects oversized requests before forwarding them to the application.

### Server-Sent Events

The Studio UI and Settings console use long-lived Server-Sent Events streams. The nginx example disables response buffering and caching for the two exact SSE routes and uses a long proxy read timeout.

Do not remove those route-specific settings unless the SSE behavior is revalidated afterward.

## 14. Existing nginx installations with other sites

Do not replace `/etc/nginx/nginx.conf` just for Web Broadcaster.

Use a dedicated virtual-host file:

```text
/etc/nginx/sites-available/web-broadcaster
```

and enable it through:

```text
/etc/nginx/sites-enabled/web-broadcaster
```

Other nginx sites can continue to use their own `server_name` blocks on TCP 80/443.

Before every nginx reload:

```bash
sudo nginx -t
```

## 15. Upgrade rules

When upgrading Web Broadcaster:

1. keep nginx and the Let's Encrypt certificate in place;
2. keep the same public hostname unless intentionally changing it;
3. preserve the runtime database directory;
4. preserve `db/.session_secret`;
5. keep the new release `start.sh` in reverse-proxy mode;
6. verify that `127.0.0.1:15000` is still the only Web Broadcaster listener;
7. run `sudo nginx -t` before any nginx reload;
8. test the public HTTPS login after the upgrade.

The nginx certificate and Certbot state live outside the Web Broadcaster release directory, so a normal application upgrade must not overwrite them.

## 16. Troubleshooting

### Browser shows connection refused

Check:

```bash
sudo systemctl status nginx --no-pager
sudo ss -ltnp | grep -E ':(80|443|15000)\b'
```

Expected:

- nginx on public/local TCP 80 and 443;
- Web Broadcaster only on `127.0.0.1:15000`.

### nginx returns 502 Bad Gateway

Check whether Web Broadcaster is running and listening on loopback:

```bash
ss -ltnp | grep ':15000'
curl -I -H 'Host: radio.example.com' http://127.0.0.1:15000/login
```

Also inspect:

```bash
sudo tail -n 100 /var/log/nginx/error.log
```

### Web Broadcaster returns 400 for the public hostname

Check that the `start.sh` value exactly contains the public hostname:

```bash
TRUSTED_HOSTS="radio.example.com"
```

and that nginx forwards `Host` and `X-Forwarded-Host` as shown in the supplied example.

### Login works locally but not through HTTPS

Confirm:

```bash
PUBLIC_INTERNET_MODE="ON"
BIND_HOST="127.0.0.1"
HTTPS_MODE="OFF"
PROXY_COUNT="1"
```

and confirm nginx sends:

```nginx
proxy_set_header X-Forwarded-Proto $scheme;
```

For the public HTTPS request `$scheme` must be `https`.

### SSE console/UI stops updating

Check that the two exact SSE locations still contain:

```nginx
proxy_buffering off;
proxy_cache off;
proxy_read_timeout 3600s;
```

Reload nginx only after:

```bash
sudo nginx -t
```

### Certbot cannot issue or renew

Check DNS first, including IPv6:

```bash
dig +short A radio.example.com
dig +short AAAA radio.example.com
```

Then verify TCP 80 is reachable from the public Internet and nginx is listening on it. Let's Encrypt HTTP-01 validation reaches port 80 even if normal HTTP traffic is ultimately redirected to HTTPS.

Inspect Certbot:

```bash
sudo certbot certificates
sudo certbot renew --dry-run
```

### Certificate renewed but nginx still presents the old certificate

Check that the deploy hook exists and is executable:

```bash
ls -l /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
```

Then validate and reload nginx manually:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 17. Security checklist

Before considering the service ready for continuous public Internet exposure, verify all of the following:

- DNS resolves the public hostname to the intended server/router.
- TCP 80 and 443 reach nginx.
- TCP 15000 is not forwarded by the router.
- Web Broadcaster listens only on `127.0.0.1:15000`.
- `PUBLIC_INTERNET_MODE="ON"`.
- `TRUSTED_HOSTS` contains the exact public hostname.
- `HTTPS_MODE="OFF"` behind nginx.
- `PROXY_COUNT="1"` for exactly one local nginx proxy.
- nginx forwards the required `X-Forwarded-*` headers.
- nginx SSE buffering is disabled for both Web Broadcaster SSE routes.
- the public certificate validates without browser warnings.
- `certbot renew --dry-run` succeeds.
- `db/.session_secret` is private and preserved.
- the first administrator has a strong, unique password.
- nginx and the operating system receive normal security updates.

## 18. Official references

The deployment design follows the documented behavior of nginx reverse proxying, Certbot's nginx plugin and renewal hooks, and Let's Encrypt HTTP-01 validation.

Official documentation:

- nginx proxy module: https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- nginx core HTTP module: https://nginx.org/en/docs/http/ngx_http_core_module.html
- Certbot user guide: https://eff-certbot.readthedocs.io/en/stable/using.html
- Certbot instructions: https://certbot.eff.org/
- Let's Encrypt challenge types: https://letsencrypt.org/docs/challenge-types/
