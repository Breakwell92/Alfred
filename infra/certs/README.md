# Extra CA certificates

Only needed on a host whose outbound HTTPS is intercepted (antivirus web scanning, a
corporate proxy). Symptom: `ollama pull` fails with `x509: certificate signed by unknown
authority`.

Put the intercepting root certificate here as a PEM file (`*.crt`), then
`docker compose up -d ollama` to recreate the container. Containers that download things
(Ollama now) trust these in addition to the system CAs. Certificates here are git-ignored;
see `docs/infra.md` for how to export one on Windows.
