FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY reader.py .
# -u = unbuffered, so docker service logs stream live
CMD ["python", "-u", "reader.py"]
