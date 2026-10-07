FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
RUN useradd --uid 10001 --create-home aerolink && mkdir /app/data && chown aerolink /app/data
USER aerolink
ENV AEROLINK_DB=/app/data/aerolink.sqlite3
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
