FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir fastapi uvicorn[standard] jinja2 httpx aiofiles python-multipart

COPY web/ ./web/

ENV API_URL=http://api:8080

CMD ["uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "3000"]
