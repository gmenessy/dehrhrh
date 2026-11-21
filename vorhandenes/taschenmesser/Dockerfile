FROM python:3.12-slim

# Tesseract mit deutschem Sprachpaket; sonst keine Systempakete
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-deu \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn>=0.30" "httpx>=0.27" "python-multipart>=0.0.9" "pypdfium2>=4.30" "pillow>=10" "olefile>=0.46"
COPY app ./app

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tm
USER 10001
ENV PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=1
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--no-server-header"]
