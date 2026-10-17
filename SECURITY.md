# Security

Please report vulnerabilities privately through GitHub:
**Security → Report a vulnerability** on this repository. Do not open a public issue.

The driver opens an HTTP server (Alpaca, port 11112) and answers Alpaca discovery on UDP 32227.
It has no authentication, as is usual for Alpaca devices, so run it only on a trusted network or bind
it to `127.0.0.1` (`[server] host` in `config.toml`).
