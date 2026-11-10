FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd -m app && mkdir -p data && chown app:app data
COPY backend/ backend/
COPY frontend/ frontend/
COPY config.yaml config.a.yaml ./
USER app
CMD ["python","-m","uvicorn","backend.app:create_app","--factory","--workers","1","--host","0.0.0.0","--port","8765"]
