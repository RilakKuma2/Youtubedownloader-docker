# Python 공식 이미지를 기반으로 사용
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 설치 (ffmpeg 등 yt-dlp 의존성)
# slim 이미지에는 기본 빌드 도구가 없을 수 있으므로 필요시 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    # 추가적으로 필요한 라이브러리가 있다면 여기에 추가 (예: libcurl4-openssl-dev 등)
    && rm -rf /var/lib/apt/lists/*

# requirements.txt 복사 및 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 코드 복사
COPY . .

# Flask 앱 기본 포트 노출 (docker-compose에서 실제 포트 매핑)
EXPOSE 5001

# 기본 CMD는 docker-compose에서 오버라이드될 수 있음
# CMD ["python", "app.py"]
