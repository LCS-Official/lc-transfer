# Source publication checks

`upstream_test_keys.json` lists hashes of unchanged public key-parser test fixtures
shipped by Primitive FTPd. These are upstream test data, not device identities or
release signing credentials, and must never be used for live authentication.
The source checker allows those exact bytes only. New/modified private keys fail.
