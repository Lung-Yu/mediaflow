FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn jinja2 httpx aiofiles

COPY web/ ./web/

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "3000"]
