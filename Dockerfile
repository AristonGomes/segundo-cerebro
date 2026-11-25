FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Remove qualquer quebra de linha do Windows (\r) que possa causar o erro 8 no Linux
RUN sed -i 's/\r$//' start.sh
RUN chmod +x start.sh

CMD ["./start.sh"]
