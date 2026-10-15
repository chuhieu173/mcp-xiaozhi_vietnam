FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# 1. Cài đặt các gói hệ thống cần thiết (ffmpeg, curl, git, gnupg) và Node.js 22 từ NodeSource
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 2. Clone và build BotGuard PO-Token Provider (v2.0.2)
RUN git clone --single-branch --branch 2.0.2 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /app/bgutil-server && \
    cd /app/bgutil-server/server && \
    npm ci && \
    npx tsc

# 3. Cài đặt các dependencies của Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. Copy toàn bộ mã nguồn vào container
COPY . .

# 5. Phân quyền thực thi cho entrypoint script
RUN chmod +x /app/entrypoint.sh

# 6. Mở cổng kết nối
EXPOSE 8000 10000

# 7. Khởi chạy thông qua entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
