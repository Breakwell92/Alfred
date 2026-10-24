# Extra CA certificates

Only needed on a host whose outbound HTTPS is intercepted (antivirus web scanning, a
corporate proxy). Symptom: `ollama pull` fails with `x509: certificate signed by unknown
authority`, or LiteLLM cloud calls fail with `SSLCertVerificationError`.

Put the intercepting root certificate here as a PEM file (`*.crt`), then
`docker compose up -d --force-recreate ollama litellm` to recreate the containers. Ollama and
LiteLLM trust these in addition to their default CAs. Certificates here are git-ignored;
see `docs/infra.md` for how to export one on Windows.
