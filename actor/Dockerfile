FROM apify/actor-python:3.12

COPY requirements.txt ./
RUN echo "Python version:" && python --version \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip freeze

COPY . ./

CMD ["python3", "-m", "src.main"]
