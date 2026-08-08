import sys
sys.path.insert(0, "/home/dk/jarvis/projects/odysseus")
from src.agents.odysseus_auth import get_session

session = get_session()
resp = session.post(
    "http://localhost:7000/api/tasks",
    json={"agent": "browser_agent", "server": "jarvis_browser", "tool": "open", "arguments": {"url": "https://example.com"}},
)
print("Status:", resp.status_code)
print("Body:", resp.text)
