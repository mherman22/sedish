FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY reader.py publisher.py publisher_fhir2.py publisher_kafka.py parity.py .
# -u = unbuffered, so docker service logs stream live.
# Default command is the CDC reader; the publisher service overrides it.
CMD ["python", "-u", "reader.py"]
