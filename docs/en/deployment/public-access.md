# Public access

## Language

- English: [Public access](public-access.md)
- Chinese: [公共接入](../../deployment/public-access.md)

## Conclusion

If inbound 443 is blocked by your ISP, use Cloudflare Tunnel first.

Use 8443 only as a temporary fallback, not as the long-term first choice.

## Key points

- blocked inbound 443 is usually a network ingress problem, not an application bug
- `8443` only changes the external port; it does not fix protocol mismatches by itself
- if you see `plain http request was sent to https port`, your HTTP/HTTPS layers are misaligned
- DNS records, Cloudflare proxy mode, and reverse-proxy source/target protocols must be checked together

## Recommended path

1. Verify the public ingress path actually works
2. Prefer Cloudflare Tunnel
3. Let NAS reverse proxy do internal forwarding only
4. Keep runtime config external so you do not rebuild the image when the access path changes

## Relation to this project

The project boundary stays the same:

- the image contains only code and dependencies
- runtime config stays external
- Shortcut only carries webhook token + business text + selector
