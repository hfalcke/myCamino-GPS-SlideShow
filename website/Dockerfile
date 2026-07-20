FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN addgroup --system --gid 10001 mycamino && adduser --system --uid 10001 --ingroup mycamino mycamino
COPY website/requirements.txt /tmp/requirements.txt
RUN pip install --requirement /tmp/requirements.txt
COPY website/ /app/
COPY docs/ /app/docs/
RUN chmod +x /app/entrypoint.sh && chown -R mycamino:mycamino /app
USER mycamino
EXPOSE 8003
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "mycamino_site.wsgi:application", "--bind", "0.0.0.0:8003", "--workers", "2", "--threads", "4", "--timeout", "60", "--access-logfile", "-"]
