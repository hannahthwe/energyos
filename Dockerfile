FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY energyos energyos
COPY web web
COPY run.py .
RUN useradd -r app
USER app
ENV PORT=8000
EXPOSE 8000
CMD ["python", "-c", "import uvicorn, run; uvicorn.run(run.app, host='0.0.0.0', port=8000)"]
