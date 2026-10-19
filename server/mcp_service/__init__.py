"""MCP 接入层：Streamable HTTP 挂在现有 FastAPI 上，Agent 经 /mcp/ 操作全部功能"""
from .app import install_mcp

__all__ = ['install_mcp']
