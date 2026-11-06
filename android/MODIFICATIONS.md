# Android modifications

Based on Primitive FTPd 8.2 by wolpi and contributors, Apache-2.0.
Source: https://github.com/wolpi/prim-ftpd/releases/tag/prim-ftpd-8.2
Original copyrights and license files are retained in the upstream source tree.

Changes include the application ID and label; a compact connection/settings screen;
transfer history and external file viewing; LAN discovery and optional user-supplied
VPN addressing; explicit public-key pairing and pinned SSH identities; shared-storage
path restrictions; Ed25519 signing compatibility; MediaStore catalog generation;
phone-side small image/video thumbnails; and Android SEND/SEND_MULTIPLE reception
with URI staging and acknowledgement through the authenticated SFTP connection.
MINA TCP_NODELAY property lookup and socket/window sizing were adjusted.

The public edition has no maintainer peer IP or subnet, no personal device name,
and no migration rule containing a private address. Protocol defaults are TCP 52022
and UDP 52023. Build scripts use configured Python/JDK/SDK locations and generate
an ignored local development signing key. No maintainer signing or pairing keys,
private build products, logs, screenshots, or transfer records are distributed.

This publication preserves upstream attribution. Third-party components retain their
own licenses; see the root NOTICE and THIRD_PARTY_NOTICES.md. New share behavior and
public defaults require both matching desktop and Android builds. Real-device
behavior with third-party share providers is not covered by desktop-only tests.
