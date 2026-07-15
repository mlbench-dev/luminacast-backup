# DNS Setup for control.luminacast.com

Add a CNAME or A record for `control.luminacast.com` pointing to the same server as `luminacast.com`.

## Option A: CNAME record (recommended if main domain has A record)
```
Type: CNAME
Name: control
Value: luminacast.com
TTL: 300
```

## Option B: A record (if using direct IP)
```
Type: A
Name: control
Value: 145.223.121.28
TTL: 300
```

## SSL Certificate

The nginx config currently uses the same Let's Encrypt cert as the main domain.
If the cert doesn't cover `control.luminacast.com`, you need to either:

1. **Expand the existing cert** (recommended):
   ```bash
   certbot certonly --nginx -d luminacast.com -d www.luminacast.com -d control.luminacast.com
   ```

2. **Or use a separate cert**:
   ```bash
   certbot certonly --nginx -d control.luminacast.com
   ```
   Then update the nginx config to point to the new cert paths.

## After DNS propagation

The control panel will be accessible at:
- https://control.luminacast.com/control (direct URL)
- https://www.luminacast.com/control (also works from main domain)
