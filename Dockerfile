FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY paper_trading_estrategia_2233.py .

CMD ["python", "paper_trading_estrategia_2233.py"]
