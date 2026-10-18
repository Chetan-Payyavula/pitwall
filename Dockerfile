# Pitwall as a container (used for the Hugging Face Space). Serves on port 7860.
FROM python:3.12-slim
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH F1_CACHE_DIR=/tmp/f1cache PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user fastf1 "fastapi>=0.110" "uvicorn[standard]>=0.29" numpy pandas
COPY --chown=user backend backend
COPY --chown=user frontend frontend
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "7860"]
