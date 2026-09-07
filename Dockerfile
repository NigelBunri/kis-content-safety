FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# ffmpeg/ffprobe for video frame sampling — same tool this logic already
# depended on inside the Django monolith.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Bake NudeNet's ONNX weights into the image at build time instead of
# leaving them to download on the first real scan in production. NudeNet's
# NudeDetector() downloads its weights into its cache dir on first
# instantiation if not already present — running that once here, during the
# build, means the resulting layer already has them cached, so no
# production request or worker startup ever depends on a cold-start network
# fetch. See README.md's "Model weights" section for why the original
# Django code never did this.
RUN python -c "from nudenet import NudeDetector; NudeDetector()"

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
