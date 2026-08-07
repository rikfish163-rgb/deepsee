# deepsee MCP server — 容器化安装(stdio)
FROM python:3.12-slim

WORKDIR /app

# 依赖:纯 stdlib,零第三方;可选 extras 需另装
COPY pyproject.toml README.md LICENSE ./
COPY deepsee ./deepsee

RUN pip install --no-cache-dir .

# stdio MCP:客户端以 docker run -i --rm deepsee 调用
ENTRYPOINT ["deepsee", "mcp"]
