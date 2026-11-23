ARG PYTHON_BASE=public.ecr.aws/docker/library/python:3.13-slim
FROM ${PYTHON_BASE}
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app.py worker.py .
EXPOSE 8080
USER nobody
CMD ["python", "app.py"]
