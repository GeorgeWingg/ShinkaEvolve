from shinka.tools.auth_status import get_authenticated_backends_summary
import json

summary = get_authenticated_backends_summary()
print(json.dumps(summary, indent=2))
