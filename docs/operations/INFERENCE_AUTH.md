# Inference server authentication

The optional `transformers_api_server.py` service authenticates inference calls
before request generation or context compression. Set `IGOREK_INFERENCE_API_KEY`
in the service's protected environment and configure the client with the same
key, through `Authorization: Bearer ...` or `X-API-Key`. This ingress key is
separate from any upstream provider credential. Conflicting headers are refused.

OpenRouter and explicit context-compression endpoints require ingress
authentication, including loopback compression proxies that may spend upstream
credit. Missing configuration fails closed before a paid request. A non-loopback
listener also requires a key before startup. Health/model-discovery endpoints
retain their existing public behavior.

Anonymous local generation with automatic local compression remains available.
Operators using an explicit compression endpoint must configure the ingress key
and client headers before applying this update. Existing processes are unaffected
until deliberately restarted.

Generated Bash calls naming server policy files allow only simple recognized
reads. Wrapped or unknown commands cannot authorize a visible policy-file
mutation. This generation-time policy is not an operating-system shell sandbox;
the executing client must retain its own filesystem and approval boundaries.
