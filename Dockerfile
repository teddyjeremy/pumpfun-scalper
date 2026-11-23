FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd -m -u 10001 bot
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY pumpbot/ pumpbot/
COPY scripts/ scripts/
COPY tests/ tests/
COPY research_ref/ research_ref/
COPY RULES.md README.md DEPLOY.md ./
RUN mkdir -p /data && chown bot:bot /data
USER bot
ENV DATA_DIR=/data MODE=paper
VOLUME ["/data"]
CMD ["python", "-m", "pumpbot.bot"]
