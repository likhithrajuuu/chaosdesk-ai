FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CHAOSDESK_HOST=0.0.0.0
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock \
    && useradd --uid 10001 --create-home app \
    && mkdir -p /app/var && chown app:app /app/var
COPY chaosdesk ./chaosdesk
COPY knowledge ./knowledge
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "chaosdesk.api"]
