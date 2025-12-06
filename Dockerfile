FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sailpoint_03_11_2025_works_100.py ./

ENTRYPOINT ["python", "sailpoint_03_11_2025_works_100.py"]
