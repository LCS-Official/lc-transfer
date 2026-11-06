# Security and privacy

This is a personal file-transfer utility, not an independently audited security product.
Use trusted local networks or a VPN. Pair only devices whose six-digit codes match.
Do not accept an unexpected changed SSH identity. The Android fork includes legacy
upstream SSH dependencies; review them before any broader deployment.

Never upload `android/signing`, private keys, `.env` files, runtime settings,
transfer history, logs, screenshots of private files, or `.lc-transfer` directories.
The public defaults contain no fixed peer address. The source checker is a guard,
not a guarantee that arbitrary future changes are free of private information.

For a suspected vulnerability, avoid posting secrets or an exploit against a live
personal device. Use a private GitHub security report if available; otherwise first
open an issue asking for a private reporting channel without sensitive details.
