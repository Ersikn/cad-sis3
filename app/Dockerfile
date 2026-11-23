FROM public.ecr.aws/docker/library/python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY app.py .
EXPOSE 8080
USER nobody
CMD ["python", "app.py"]
