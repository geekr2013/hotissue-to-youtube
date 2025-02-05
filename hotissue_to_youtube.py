import requests
from bs4 import BeautifulSoup
import re
import os
import time
import pickle
import textwrap
import logging
import tempfile
import random
import functools
import sys
print("sys.path:", sys.path)
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from moviepy.editor import (VideoFileClip, ColorClip, CompositeVideoClip, 
                            TextClip, concatenate_videoclips, AudioFileClip)
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# -------------------------------
# 통합 설정
# -------------------------------
CONFIG = {
    "youtube_tags": ["#쇼츠", "#핫클립", "#인스타픽", "#짤툰", "#데일리짤", "#유머짤", "#이슈캐치"],
    "youtube_category_id": "22",
    "target_url": "https://aagag.com/issue/",
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "target_width": 1080,
    "target_height": 1920,
    "max_videos": 10,
    "landscape_threshold": 1.5,
    "font_path": "/Users/user/Downloads/Hakgyoansim_Nadeuri.otf",
    "youtube_description": "🔥 짧은 순간에 큰 웃음! 🚀\n매일 업데이트되는 핫한 이슈와 소소한 재미를 만나보세요!\n좋아요 ❤️와 구독 부탁드려요! 🛎️",
    "retry_count": 3,
    "upload_interval": 30,
    "background_music_path": "/Users/user/Downloads/background_music.mp3"
}

# -------------------------------
# 로깅 설정
# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("app.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# -------------------------------
# 재시도 데코레이터 (반환값이 False일 경우 재시도)
# -------------------------------
def retry_on_false(tries=3, delay=10, backoff=1):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            _tries, _delay = tries, delay
            result = func(*args, **kwargs)
            while result is False and _tries > 1:
                _tries -= 1
                logging.warning(f"{func.__name__} 실패. {_tries}회 재시도 남음. {_delay}초 후 재시도합니다.")
                time.sleep(_delay)
                _delay *= backoff
                result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator

# -------------------------------
# 배경색에 따른 최적 텍스트 색상 결정 함수 (요구사항 17)
# -------------------------------
def get_optimal_text_color(background_color=(0, 0, 0)):
    r, g, b = background_color
    brightness = (0.299 * r + 0.587 * g + 0.114 * b)
    return "#000000" if brightness > 128 else "#FFD700"

# -------------------------------
# 유머러스한 메타데이터 생성기 (요구사항 18)
# -------------------------------
def generate_humorous_metadata():
    humorous_lines = [
        "세상에서 가장 웃긴 영상입니다!",
        "하루의 피로를 싹 날려줄 유머!",
        "웃음이 절로 나는 마법의 영상!",
        "이 영상 보고 웃음 참지 못할걸요?",
        "당신의 기분을 200% 상승시켜줄 영상!"
    ]
    return random.choice(humorous_lines)

# -------------------------------
# 제목 정제: 확장자 및 관련 단어 제거 (요구사항 1)
# -------------------------------
def remove_extension(title):
    title = re.sub(r'^[🔥]+', '', title).strip()
    title = re.sub(r'\.(gif|mov|mp4|m4v|avi|flv|webm)$', '', title, flags=re.IGNORECASE).strip()
    title = re.sub(r'(?i)(?<!\w)(gif|mov|avi|mp4|m4v|flv|webm)(?!\w)', '', title)
    title = re.sub(r'\s+', ' ', title).strip()
    return title

# -------------------------------
# 다운로드 함수 (요구사항 13 적용: 3회 재시도)
# -------------------------------
@retry_on_false(tries=3, delay=10, backoff=1)
def download_file(url, output_path):
    try:
        response = requests.get(url, stream=True, timeout=15)
        response.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        logging.info(f"다운로드 성공: {os.path.basename(output_path)}")
        return True
    except Exception as e:
        logging.error(f"다운로드 실패: {str(e)}")
        return False

# -------------------------------
# YouTube 인증 함수
# -------------------------------
def authenticate_youtube():
    creds = None
    token_path = 'token.pickle'
    if os.path.exists(token_path):
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', ["https://www.googleapis.com/auth/youtube.upload"])
        creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)
    return build('youtube', 'v3', credentials=creds)

# -------------------------------
# 업로드 함수 (유머러스한 메타데이터 추가 및 재시도)
# -------------------------------
def upload_to_youtube(youtube, video_file, title, description, delete_after_upload=True):
    cleaned_title = remove_extension(title)[:97]
    upload_title = cleaned_title
    description = description + "\n" + generate_humorous_metadata()
    
    body = {
        'snippet': {
            'title': upload_title,
            'description': description,
            'tags': CONFIG["youtube_tags"],
            'categoryId': CONFIG["youtube_category_id"]
        },
        'status': {'privacyStatus': 'public'}
    }
    
    success = False
    try:
        for attempt in range(CONFIG["retry_count"]):
            try:
                media = MediaFileUpload(video_file, chunksize=-1, resumable=True)
                request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        logging.info(f"진행률: {int(status.progress() * 100)}%")
                logging.info(f"업로드 성공: {response['id']}")
                time.sleep(CONFIG["upload_interval"])
                success = True
                break
            except Exception as e:
                logging.warning(f"업로드 재시도 {attempt+1}/{CONFIG['retry_count']}: {str(e)}")
                time.sleep(10)
        return success
    finally:
        if success and delete_after_upload and os.path.exists(video_file):
            os.remove(video_file)
            logging.info(f"로컬 파일 삭제: {video_file}")

# -------------------------------
# 게시글 내 영상 컨텐츠 수집 (영상만 수집)
# -------------------------------
def process_post(link):
    try:
        response = requests.get(link, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        title = re.sub(r'[^\w\s가-힣]', ' ', soup.find("title").get_text(strip=True))
        title = remove_extension(title)
        media_content = []
        for video in soup.find_all(['video', 'source']):
            if video.get('src'):
                media_content.append(('video', video['src']))
        if not media_content:
            og_video = soup.find("meta", property="og:video")
            if og_video and og_video.get("content"):
                media_content.append(('video', og_video['content']))
        return [(typ, url, title) for typ, url in media_content]
    except Exception as e:
        logging.error(f"게시물 처리 오류: {str(e)}")
        return None

# -------------------------------
# 비디오 편집 함수 (오버레이 텍스트 크기는 target_h의 5%로 설정)
# -------------------------------
@retry_on_false(tries=3, delay=10, backoff=1)
def process_video(input_path, output_path, title):
    try:
        font_path = CONFIG["font_path"] if os.path.exists(CONFIG["font_path"]) else None
        clip = VideoFileClip(input_path)
        if clip.duration > 60: 
            clip = clip.subclip(0, 60)
        target_w = CONFIG["target_width"]  # 1080
        target_h = CONFIG["target_height"] # 1920
        clip = clip.resize(width=target_w)
        background = ColorClip((target_w, target_h), color=(0, 0, 0)).set_duration(clip.duration)
        final_clip = CompositeVideoClip([background, clip.set_position('center')], size=(target_w, target_h))
        
        final_audio = None
        if clip.audio:
            final_audio = clip.audio
        else:
            if os.path.exists(CONFIG["background_music_path"]):
                bg_music = AudioFileClip(CONFIG["background_music_path"])
                bg_music = bg_music.volumex(0.3).subclip(0, clip.duration)
                final_audio = bg_music
            else:
                logging.warning("배경음악 파일 없음: 무음 영상 생성")
        if final_audio:
            final_clip = final_clip.set_audio(final_audio)
        
        text_color = get_optimal_text_color((0, 0, 0))
        computed_fontsize = int(target_h * 0.05)
        try:
            title_clip = TextClip(
                textwrap.fill(title, width=16),
                fontsize=computed_fontsize,
                color=text_color,
                font=font_path,
                method='label',
                align='center',
                stroke_color='black',
                stroke_width=2
            )
        except Exception as e:
            logging.warning(f"폰트 오류 ({str(e)}), 기본 폰트 사용")
            title_clip = TextClip(
                textwrap.fill(title, width=16),
                fontsize=computed_fontsize,
                color=text_color,
                font='AppleGothic',
                method='label',
                align='center',
                stroke_color='black',
                stroke_width=2
            )
        title_clip = title_clip.set_position(('center', target_h * 0.1)).set_duration(clip.duration)
        final_video = CompositeVideoClip([final_clip, title_clip])
        final_video.write_videofile(output_path, codec="libx264", threads=4)
        return True
    except Exception as e:
        logging.error(f"비디오 처리 실패: {str(e)}")
        return False
    finally:
        if 'clip' in locals():
            clip.close()
        if 'final_video' in locals():
            final_video.close()
        if 'bg_music' in locals():
            bg_music.close()

# -------------------------------
# 수정된 fetch_post_links() 함수
# (GitHub Actions 환경에서 Selenium이 안정적으로 작동하도록 Chrome 옵션을 추가)
# -------------------------------
def fetch_post_links():
    options = Options()
    # 기본 headless 옵션 외 추가 옵션 설정 (GitHub Actions의 Ubuntu 환경에 적합)
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"user-agent={CONFIG['user_agent']}")
    # Chromium 브라우저가 설치되어 있다면 바이너리 위치 설정 (GitHub Actions에서는 이 경로가 보통 유효합니다)
    options.binary_location = "/usr/bin/chromium-browser"
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(CONFIG["target_url"])
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CLASS_NAME, 'article')))
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        return list({f"https://aagag.com{post['href']}" for post in soup.find_all('a', class_='article t', href=True)})
    except Exception as e:
        logging.error(f"페이지 로드 실패: {str(e)}")
        return []
    finally:
        if 'driver' in locals():
            driver.quit()

# -------------------------------
# 메인 처리 함수
# -------------------------------
def main():
    youtube = authenticate_youtube()
    post_links = fetch_post_links()[:CONFIG["max_videos"] * 3]
    
    shorts_video_paths = []
    shorts_video_titles = []
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(process_post, link): link for link in post_links}
        for future in as_completed(futures):
            result = future.result()
            if not result or len(shorts_video_paths) >= CONFIG["max_videos"]:
                continue
            for content_type, content_url, title in result:
                if content_type != 'video':
                    continue
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_file:
                    temp_path = temp_file.name
                try:
                    if download_file(content_url, temp_path):
                        output_filename = f"{title}.mp4"
                        if process_video(temp_path, output_filename, title):
                            shorts_video_paths.append(output_filename)
                            shorts_video_titles.append(title)
                finally:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                if len(shorts_video_paths) >= CONFIG["max_videos"]:
                    break

    # 개별 쇼츠 영상 업로드 (파일은 유지하고 업로드 후 나중에 삭제)
    shorts_upload_success_count = 0
    for video in shorts_video_paths:
        video_title = os.path.splitext(os.path.basename(video))[0]
        if upload_to_youtube(youtube, video, video_title, CONFIG["youtube_description"], delete_after_upload=False):
            logging.info(f"쇼츠 영상 업로드 성공: {video}")
            shorts_upload_success_count += 1
        else:
            logging.warning(f"쇼츠 영상 업로드 실패: {video}")

    # === BEGIN: Merge Shorts into Normal Video Step ===
    merged_upload_success = False
    if shorts_video_paths and shorts_upload_success_count == len(shorts_video_paths):
        try:
            merged_clips = []
            for vp in shorts_video_paths:
                clip = VideoFileClip(vp)
                clip_resized = clip.resize(height=1080)
                background = ColorClip((1920, 1080), color=(0, 0, 0)).set_duration(clip_resized.duration)
                composite = CompositeVideoClip([background, clip_resized.set_position('center')])
                merged_clips.append(composite)
                # clip.close()는 composite가 clip 데이터를 필요로 하므로 여기서 바로 닫지 않습니다.
            if merged_clips:
                merged_video = concatenate_videoclips(merged_clips)
                merged_video_filename = "merged_normal.mp4"
                merged_video.write_videofile(merged_video_filename, codec="libx264")
                merged_video.close()
                merged_title = "합본: " + " / ".join(shorts_video_titles)
                if len(merged_title) > 100:
                    merged_title = merged_title[:100]
                if upload_to_youtube(youtube, merged_video_filename, merged_title, CONFIG["youtube_description"]):
                    logging.info(f"합본 영상 업로드 성공: {merged_video_filename}")
                    merged_upload_success = True
                else:
                    logging.warning(f"합본 영상 업로드 실패: {merged_video_filename}")
            for comp in merged_clips:
                comp.close()
        except Exception as e:
            logging.error(f"합본 영상 처리/업로드 실패: {str(e)}")
    else:
        logging.warning("모든 쇼츠 영상이 업로드되지 않아 합본 영상을 생성하지 않습니다.")
    # === END: Merge Shorts into Normal Video Step ===

    for video in shorts_video_paths:
        if os.path.exists(video):
            os.remove(video)
            logging.info(f"로컬 쇼츠 영상 파일 삭제: {video}")
    if os.path.exists("merged_normal.mp4"):
        os.remove("merged_normal.mp4")
        logging.info("로컬 합본 영상 파일 삭제: merged_normal.mp4")
    
    total_success = shorts_upload_success_count + (1 if merged_upload_success else 0)
    logging.info(f"최종 업로드 성공: 쇼츠 영상 {shorts_upload_success_count}개, 합본 영상 {1 if merged_upload_success else 0}개, 총 {total_success}개 업로드 완료.")

if __name__ == "__main__":
    main()
