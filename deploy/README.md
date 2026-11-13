# Hosting

`nginx.conf` is an example, replace `example.com` with your domain.

The game only accepts a trusted certificate (a self-signed one gives error 806-2728), so use a real domain and Let's Encrypt:

```
sudo certbot certonly --nginx -d booster.example.com -d realtime.example.com
```

The booster runs on `127.0.0.1:18084`, `/cdn` is served from `resources/cdn/`.

The realtime server is not HTTP, add this to the `stream {}` block:

```
stream {
    server {
        listen 11401;
        proxy_protocol on;
        proxy_pass 127.0.0.1:18401;
    }
}
```

Run the three services with systemd or similar.
