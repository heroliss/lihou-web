FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    FLASK_CONFIG=public-demo LIHOU_DEMO_DIR=/tmp/lihou-demo PORT=7860
RUN useradd --create-home --uid 1000 demo
WORKDIR /app
COPY requirements.txt ./
COPY requirements ./requirements
RUN pip install --no-cache-dir -r requirements/public-demo.txt
COPY --chown=demo:demo . .
USER demo
EXPOSE 7860
CMD ["sh", "-c", "python manage.py prepare-public-demo && exec gunicorn --workers 1 --threads 2 --bind 0.0.0.0:${PORT:-7860} manage:app"]
