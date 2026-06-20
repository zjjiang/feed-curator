# feed-curator 镜像:单阶段构建。
# 基础镜像走 docker daemon 配置的 registry-mirrors(国内加速,见 docs/deployment.md)。
# uv 经清华 PyPI 源安装,依赖全程走国内,避免境外 registry 超时。
FROM python:3.14-slim-bookworm

# pip 走清华源;curl 供 healthcheck。
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=120

# apt 换清华源(deb.debian.org 在本网络拉 main Packages 索引会卡),再装 curl + uv。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

# 先装依赖(利用层缓存);uv 用同一清华源(UV_INDEX_URL)。
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 再拷代码、装项目本身。
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 9003

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9003"]
