# Security policy

Report suspected vulnerabilities privately to security@january.ai. Include the
package/Python versions and a minimal reproduction using synthetic data. Never
include API keys, client tokens, food records or full response payloads in a report.

Keep server keys on trusted backends. Use your platform's secret manager in
production and an ignored .env file locally. The SDK never loads .env itself.
Rotate exposed credentials through the January developer dashboard.

Exception strings and model representations are redacted. Explicitly inspecting
model fields, error causes or caller-supplied HTTP clients can reveal sensitive
data; do not serialize those into telemetry or logs. Redirects are not followed.

Only pass trusted filesystem paths to photo preparation. For end-user uploads,
prefer bytes or file objects supplied by your upload handler. URL/data-URI inputs
are forwarded unchanged, including metadata. Compliant small files also retain
metadata; pass a Pillow image to force re-encoding and EXIF removal.

Install compatible maintenance releases and review CHANGELOG.md for changes.
