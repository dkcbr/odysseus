import os
import json
import asyncio
from fastmcp import FastMCP

def handle_ping(args):
    return {"response": f"Pong: {args.get('message', '')}"}

def handle_default(args):
    return {"response": "OK"}

TOOLS = {
    "ping": handle_ping,
}

def handle_request(tool_name, args):
    handler = TOOLS.get(tool_name, handle_default)
    return handler(args)
