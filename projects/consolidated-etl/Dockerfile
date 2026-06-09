FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mapping.py db.py state.py etl.py .
CMD ["python", "-u", "etl.py"]
