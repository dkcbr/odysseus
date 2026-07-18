FROM node:20-slim
WORKDIR /app
# mcp-remote proxies real MCP protocol to https://mcp.trader.dev/sse --
# same proven mechanism already working in the JARVIS REPL.
CMD ["npx", "-y", "mcp-remote", "https://mcp.trader.dev/sse"]
