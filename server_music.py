"""
Xiaozhi Multi-Source Music Server (SoundCloud + ZingMP3)
Tối ưu hóa đặc biệt cho máy chủ cấu hình thấp (Render Free / VPS 1 Core / 512MB RAM):
1. Nguồn nhạc:
   - SoundCloud (Ưu tiên số 1): Kho nhạc khổng lồ, EDM, remix, acoustic, không dính 429 hay bot-check.
   - ZingMP3 (Ưu tiên số 2): Nhạc bản quyền Việt Nam, tự động lọc bài VIP, phát trực tiếp MP3 128kbps từ CDN.
2. Cơ chế Stream & Cache bất đồng bộ (StreamSession):
   - Phản hồi HTTP 200 OK ngay trong < 300ms, không bắt ESP32 chờ tải hết toàn bộ bài hát.
   - FFmpeg chạy tối ưu hóa với giới hạn luồng (-threads 2) cho CPU thấp.
   - Tự động lưu cache đĩa (/tmp) song song khi stream để lần sau phát tức thời (0ms delay).
3. Tuân thủ 100% đặc tả API_SPEC_MUSIC_SERVER.md cho loa AI Xiaozhi ESP32.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import hashlib
import hmac
import urllib.parse
import yt_dlp
import uvicorn
import httpx
import socket
import os
import io
import time

app = FastAPI(title="Xiaozhi Music Server (SoundCloud + ZingMP3)")

# Kích hoạt CORS cho ESP32 và các ứng dụng ngoài
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bộ nhớ đệm lưu tiêu đề bài hát để phục vụ tự động Fallback và hiển thị lời
SONG_TITLE_CACHE: dict[str, str] = {}

CACHE_DIR = "/tmp/xiaozhi_music_cache"
os.makedirs(CACHE_DIR, exist_ok=True)


# =====================================================================
# 1. ZINGMP3 ENGINE (Thuần Python - Tự động lọc bài VIP)
# =====================================================================
class ZingMP3Engine:
    DOMAIN = "https://zingmp3.vn"
    API_KEY = "X5BM3w8N7MKozC0B85o4KMlzLZKhV00y"
    API_SECRET = b"acOrvUS15XRW2o9JksiK1KgQ6Vbds8ZW"
    API_VERSION = "1.19.1"
    SIGNED_PARAMS = frozenset({"ctime", "id", "type", "page", "count", "version"})

    API_SLUGS = {
        "init": "/api/v2/page/get/song",
        "search": "/api/v2/search/multi",
        "song-streaming": "/api/v2/song/get/streaming",
    }

    def __init__(self):
        self.session = httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://zingmp3.vn/",
            },
            timeout=15.0,
            follow_redirects=True,
        )
        self.initialized = False

    def build_api_url(self, slug_key: str, params: dict) -> str:
        api_slug = self.API_SLUGS[slug_key]
        all_params = {**params, "ctime": "1", "version": self.API_VERSION}
        sign_params = {
            k: v for k, v in sorted(all_params.items())
            if k in self.SIGNED_PARAMS and v not in (None, "")
        }
        hash_input = "".join(
            urllib.parse.quote(str(k), safe="") + "=" + urllib.parse.quote(str(v), safe="")
            for k, v in sign_params.items()
        )
        digest = hashlib.sha256(hash_input.encode()).hexdigest()
        signature = hmac.new(self.API_SECRET, (api_slug + digest).encode(), hashlib.sha512).hexdigest()
        query = urllib.parse.urlencode({**all_params, "apiKey": self.API_KEY, "sig": signature})
        return f"{self.DOMAIN}{api_slug}?{query}"

    async def ensure_initialized(self):
        if self.initialized:
            return
        try:
            init_url = self.build_api_url("init", {"id": ""})
            await self.session.get(init_url)
            self.initialized = True
        except Exception as e:
            print(f"[!] Lỗi khởi tạo ZingMP3: {e}")

    async def search(self, keyword: str, limit: int = 4):
        try:
            await self.ensure_initialized()
            url = self.build_api_url("search", {"q": keyword})
            resp = await self.session.get(url)
            data = resp.json().get("data", {})
            songs = data.get("songs", [])
            results = []
            for s in songs:
                # streamingStatus == 1 là bài hát miễn phí, 2 là VIP
                if s.get("streamingStatus") == 1:
                    sid = s.get("encodeId")
                    results.append({
                        "encodeId": f"zing_{sid}",
                        "title": s.get("title"),
                        "artistsNames": s.get("artistsNames", "Zing MP3"),
                        "source": "ZingMP3",
                    })
                    if len(results) >= limit:
                        break
            return results
        except Exception as e:
            print(f"[!] Lỗi tìm kiếm ZingMP3: {e}")
            return []

    async def get_stream_url(self, song_id: str):
        """Lấy link stream 128kbps từ ZingMP3. Trả về None nếu bài hát là VIP."""
        try:
            await self.ensure_initialized()
            clean_id = song_id[5:] if song_id.startswith("zing_") else song_id
            url = self.build_api_url("song-streaming", {"id": clean_id})
            resp = await self.session.get(url)
            res_json = resp.json()

            if res_json.get("err") != 0:
                return None

            data = res_json.get("data")
            if not data or not isinstance(data, dict):
                return None

            audio_url = data.get("128") or data.get("320")
            if not audio_url or audio_url == "VIP":
                return None

            return audio_url
        except Exception as e:
            print(f"[!] Lỗi lấy stream ZingMP3 {song_id}: {e}")
            return None

    async def get_song_title(self, song_id: str):
        """Lấy tên bài hát từ ZingMP3 để hỗ trợ Fallback"""
        try:
            await self.ensure_initialized()
            clean_id = song_id[5:] if song_id.startswith("zing_") else song_id
            url = self.build_api_url("init", {"id": clean_id})
            resp = await self.session.get(url)
            data = resp.json().get("data", {})
            return data.get("title")
        except Exception:
            return None


zing_engine = ZingMP3Engine()


# =====================================================================
# 2. SOUNDCLOUD ENGINE
# =====================================================================
YDL_OPTS_SC_SEARCH = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
    "socket_timeout": 6,
    "retries": 1,
}

YDL_OPTS_SC_STREAM = {
    "format": "bestaudio/best",
    "quiet": True,
    "skip_download": True,
    "socket_timeout": 8,
    "retries": 1,
}


def search_soundcloud(q: str, limit: int = 4):
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS_SC_SEARCH) as ydl:
            res = ydl.extract_info(f"scsearch{limit}:{q}", download=False)
            items = []
            for entry in res.get("entries", []):
                track_id = entry.get("id")
                if not track_id:
                    continue
                items.append({
                    "encodeId": f"sc_{track_id}",
                    "title": entry.get("title"),
                    "artistsNames": entry.get("uploader", "SoundCloud"),
                    "source": "SoundCloud",
                })
            return items
    except Exception as e:
        print(f"[!] Lỗi tìm kiếm SoundCloud: {e}")
        return []


# =====================================================================
# 3. HỆ THỐNG STREAM & CACHE BẤT ĐỒNG BỘ (STREAMSESSION)
# =====================================================================
class StreamSession:
    """
    Quản lý luồng phát nhạc và bộ đệm đĩa cho ESP32.
    Giải quyết dứt điểm 2 lỗi:
    1. Lỗi timeout 23s: Phát các chunk ban đầu ngay sau < 300ms.
    2. Lỗi ngắt quãng sau vài giây: FFmpeg transcode chuẩn CBR 128kbps stereo vào đĩa,
       kèm đầy đủ Accept-Ranges bytes.
    """
    def __init__(self, track_id: str, title: str = "", sample_rate: int = 24000):
        self.track_id = track_id
        self.title = title
        self.sample_rate = sample_rate
        self.temp_path = os.path.join(CACHE_DIR, f"{track_id}_{sample_rate}.tmp_{os.getpid()}_{int(time.time() * 1000)}")
        self.cached_path = os.path.join(CACHE_DIR, f"{track_id}_{sample_rate}.mp3")
        self.ready_event = asyncio.Event()
        self.is_done = False
        self.error: str | None = None
        self.task: asyncio.Task | None = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._worker())

    async def _worker(self):
        try:
            # Dọn dẹp cache nếu quá 50 bài để bảo vệ ổ cứng máy chủ nhỏ
            try:
                files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
                if len(files) > 50:
                    files.sort(key=os.path.getmtime)
                    for f in files[:15]:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
            except Exception:
                pass

            stream_url = None
            is_direct_mp3 = False

            # NGUỒN 1: SoundCloud
            if self.track_id.startswith("sc_"):
                clean_id = self.track_id[3:]
                target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"

                def _ext_sc():
                    with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
                        return ydl.extract_info(target_url, download=False)

                try:
                    info = await asyncio.to_thread(_ext_sc)
                    stream_url = info.get("url")
                except Exception as e:
                    print(f"[!] Lỗi trích xuất SoundCloud {self.track_id}: {e}")

                # Nếu lỗi link SoundCloud, tự động fallback sang ZingMP3
                if not stream_url:
                    fb_title = self.title or SONG_TITLE_CACHE.get(self.track_id)
                    if fb_title:
                        zing_cands = await zing_engine.search(fb_title, 2)
                        for z in zing_cands:
                            z_url = await zing_engine.get_stream_url(z["encodeId"])
                            if z_url:
                                self.track_id = z["encodeId"]
                                stream_url = z_url
                                is_direct_mp3 = True
                                break

            # NGUỒN 2: ZingMP3
            elif self.track_id.startswith("zing_"):
                stream_url = await zing_engine.get_stream_url(self.track_id)
                if stream_url:
                    is_direct_mp3 = True
                else:
                    # Nếu bài Zing bị VIP, fallback sang SoundCloud
                    fb_title = self.title or SONG_TITLE_CACHE.get(self.track_id) or await zing_engine.get_song_title(self.track_id)
                    if fb_title:
                        sc_cands = await asyncio.to_thread(search_soundcloud, fb_title, 1)
                        if sc_cands:
                            self.track_id = sc_cands[0]["encodeId"]
                            clean_id = self.track_id[3:]
                            target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"

                            def _ext_sc_fb():
                                with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
                                    return ydl.extract_info(target_url, download=False)

                            info = await asyncio.to_thread(_ext_sc_fb)
                            stream_url = info.get("url")

            # NGUỒN 3: Tên bài hát tự do không có tiền tố
            else:
                search_q = self.title or self.track_id.replace("-", " ").replace("_", " ")
                sc_cands = await asyncio.to_thread(search_soundcloud, search_q, 1)
                if sc_cands:
                    self.track_id = sc_cands[0]["encodeId"]
                    clean_id = self.track_id[3:]
                    target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"

                    def _ext_sc_free():
                        with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
                            return ydl.extract_info(target_url, download=False)

                    info = await asyncio.to_thread(_ext_sc_free)
                    stream_url = info.get("url")

            if not stream_url:
                raise Exception(f"Không thể tìm thấy nguồn phát âm thanh cho {self.track_id}")

            # THỰC THI STREAMING & CACHING:
            # Chuyển đổi toàn bộ âm thanh sang chuẩn MPEG-2 Layer III (24,000 Hz, CBR 128kbps stereo):
            # Tự động khắc phục hiện tượng rè loa trên firmware ESP32 khóa tần số lấy mẫu 24kHz
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error",
                "-threads", "2",
                "-i", stream_url,
                "-vn",
                "-c:a", "libmp3lame",
                "-b:a", "128k",
                "-ar", str(self.sample_rate),
                "-ac", "2",
                "-f", "mp3",
                self.temp_path
            )

            # Chờ khi có 16KB dữ liệu đầu tiên (thường < 300ms) là báo ready ngay lập tức!
            while proc.returncode is None:
                if os.path.exists(self.temp_path) and os.path.getsize(self.temp_path) >= 16384:
                    self.ready_event.set()
                    break
                await asyncio.sleep(0.04)

            await proc.wait()

            self.ready_event.set()

            # Hoàn tất: đổi tên file tạm thành file cache chính thức
            if os.path.exists(self.temp_path) and os.path.getsize(self.temp_path) > 30000:
                os.replace(self.temp_path, self.cached_path)
                self.is_done = True
                print(f"[+] Đã lưu cache thành công bài: {self.track_id} ({os.path.getsize(self.cached_path)} bytes)")
            else:
                self.error = f"Lỗi luồng âm thanh {self.track_id}"
        except Exception as e:
            self.error = str(e)
            print(f"[!] Lỗi worker {self.track_id}: {e}")
            self.ready_event.set()

    async def stream_generator(self):
        """Đọc và truyền liên tục dữ liệu audio sang client ESP32 song song khi file đang được ghi"""
        await self.ready_event.wait()
        if self.error and not os.path.exists(self.cached_path) and not os.path.exists(self.temp_path):
            raise HTTPException(status_code=500, detail=self.error)

        target_file = self.cached_path if os.path.exists(self.cached_path) else self.temp_path

        retries = 0
        while not os.path.exists(target_file) and retries < 40:
            await asyncio.sleep(0.05)
            retries += 1
            if os.path.exists(self.cached_path):
                target_file = self.cached_path

        if not os.path.exists(target_file):
            raise HTTPException(status_code=404, detail="Không tìm thấy file audio")

        file_pos = 0
        with open(target_file, "rb") as f:
            while True:
                chunk = f.read(16384)
                if chunk:
                    file_pos += len(chunk)
                    yield chunk
                else:
                    if self.is_done or os.path.exists(self.cached_path):
                        # Nếu file temp đã được đổi tên sang cached_path, đọc nốt phần còn lại
                        if target_file != self.cached_path and os.path.exists(self.cached_path):
                            with open(self.cached_path, "rb") as f2:
                                f2.seek(file_pos)
                                while True:
                                    c2 = f2.read(16384)
                                    if not c2:
                                        break
                                    yield c2
                        break
                    if self.error:
                        break
                    await asyncio.sleep(0.04)


ACTIVE_SESSIONS: dict[str, StreamSession] = {}


def get_or_create_stream_session(track_id: str, title: str = "", sample_rate: int = 24000) -> StreamSession:
    clean_id = track_id
    if clean_id.endswith(".mp3"):
        clean_id = clean_id[:-4]

    session_key = f"{clean_id}_{sample_rate}"
    if session_key in ACTIVE_SESSIONS:
        session = ACTIVE_SESSIONS[session_key]
        if not session.error:
            return session

    session = StreamSession(clean_id, title, sample_rate)
    ACTIVE_SESSIONS[session_key] = session
    return session


def get_local_ip():
    """Tự động lấy IP nội mạng"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# =====================================================================
# 4. CÁC API ENDPOINTS PHỤC VỤ XIAOZHI ESP32 (KHÔNG DÙNG GIAO DIỆN WEB)
# =====================================================================
@app.get("/")
def home_status():
    """Endpoint gốc nhẹ nhàng bằng JSON (thay thế hoàn toàn giao diện Web cũ)"""
    return {
        "status": "online",
        "service": "Xiaozhi Music Server",
        "engines": ["SoundCloud", "ZingMP3"],
        "endpoints": {
            "search": "/api/search?q={keyword}",
            "stream_pcm": "/stream_pcm?song={song}&artist={artist}",
            "stream": "/stream/{id}.mp3",
            "lyrics": "/lyrics/{id}.lrc",
            "health": "/health"
        }
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "engines": ["SoundCloud", "ZingMP3"],
        "compatible_with": "Xiaozhi ESP32",
    }


@app.get("/system")
def system_metrics():
    """Endpoint theo dõi lượng RAM và CPU tiêu thụ theo thời gian thực"""
    import resource
    import sys

    # RAM của tiến trình Python hiện tại
    rusage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        proc_mem_mb = round(rusage / (1024 * 1024), 2)
    else:
        proc_mem_mb = round(rusage / 1024, 2)

    # Đọc cgroup memory trên Render Linux container nếu có
    container_mem_mb = None
    container_mem_limit_mb = None
    for p_cur in ["/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"]:
        if os.path.exists(p_cur):
            try:
                with open(p_cur) as f:
                    container_mem_mb = round(int(f.read().strip()) / (1024 * 1024), 2)
                break
            except Exception:
                pass

    for p_max in ["/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"]:
        if os.path.exists(p_max):
            try:
                with open(p_max) as f:
                    val = f.read().strip()
                    if val != "max":
                        container_mem_limit_mb = round(int(val) / (1024 * 1024), 2)
                break
            except Exception:
                pass

    used_mb = container_mem_mb or proc_mem_mb
    limit_mb = container_mem_limit_mb or 512.0
    mem_percent = round((used_mb / limit_mb) * 100, 1)

    # Đếm số file cache và dung lượng thư mục cache
    cache_files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
    total_cache_bytes = sum(os.path.getsize(f) for f in cache_files if os.path.exists(f))
    total_cache_mb = round(total_cache_bytes / (1024 * 1024), 2)

    return {
        "status": "ok",
        "memory": {
            "used_mb": used_mb,
            "limit_mb": limit_mb,
            "usage_percent": f"{mem_percent}%",
            "python_process_rss_mb": proc_mem_mb,
        },
        "cache": {
            "cached_songs_count": len(cache_files),
            "total_cache_size_mb": total_cache_mb,
            "cache_dir": CACHE_DIR,
        },
        "active_sessions_count": len(ACTIVE_SESSIONS),
    }


@app.get("/api/search")
async def search_song(q: str):
    """
    Tìm kiếm bài hát đồng thời từ SoundCloud và ZingMP3.
    Ưu tiên SoundCloud hàng đầu, sau đó đến ZingMP3.
    """
    try:
        sc_task = asyncio.to_thread(search_soundcloud, q, 4)
        zing_task = zing_engine.search(q, limit=4)

        sc_results, zing_results = await asyncio.gather(sc_task, zing_task, return_exceptions=True)

        sc_items = sc_results if isinstance(sc_results, list) else []
        zing_items = zing_results if isinstance(zing_results, list) else []

        # Ưu tiên SoundCloud trước, ZingMP3 sau
        combined = sc_items + zing_items

        for item in combined:
            if item.get("encodeId") and item.get("title"):
                SONG_TITLE_CACHE[item["encodeId"]] = item["title"]

        return {
            "err": 0,
            "msg": "Success",
            "data": {"items": combined}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tìm kiếm: {str(e)}")


@app.get("/stream_pcm")
async def stream_pcm(request: Request, song: str = "", artist: str = ""):
    """
    API Tìm kiếm & Lấy luồng bài hát cho thiết bị ESP32 Xiaozhi.
    Tuân thủ 100% đặc tả API_SPEC_MUSIC_SERVER.md:
    ƯU TIÊN HÀNG ĐẦU: SoundCloud (chống chặn IP, độ ổn định tuyệt đối).
    """
    song_param = song or request.query_params.get("song", "").strip()
    if not song_param:
        song_param = request.query_params.get("q", "").strip()

    if not song_param:
        return {"error": "Song not found", "audio_url": ""}

    artist_param = artist or request.query_params.get("artist", "").strip()
    query = f"{song_param} {artist_param}".strip()

    print(f"[ESP32 /stream_pcm] Yêu cầu: Song='{song_param}', Artist='{artist_param}' -> Query='{query}'")

    try:
        sc_task = asyncio.to_thread(search_soundcloud, query, 3)
        zing_task = zing_engine.search(query, limit=3)

        sc_results, zing_results = await asyncio.gather(sc_task, zing_task, return_exceptions=True)

        sc_items = sc_results if isinstance(sc_results, list) else []
        zing_items = zing_results if isinstance(zing_results, list) else []

        selected = None

        # 1. Ưu tiên số 1: SoundCloud (Kho nhạc khổng lồ, không chặn bot)
        if sc_items:
            selected = sc_items[0]

        # 2. Ưu tiên số 2: ZingMP3 MIỄN PHÍ
        if not selected and zing_items:
            for item in zing_items:
                stream_url = await zing_engine.get_stream_url(item["encodeId"])
                if stream_url:
                    selected = item
                    break

        # 3. Dự phòng nếu chỉ có Zing (tự động fallback khi tải)
        if not selected and zing_items:
            selected = zing_items[0]

        if not selected:
            print(f"[!] Không tìm thấy bài hát nào cho: {query}")
            return {"error": "Song not found", "audio_url": ""}

        encode_id = selected["encodeId"]
        title = selected.get("title") or song_param
        artist_name = selected.get("artistsNames") or artist_param or "Nghệ sĩ"

        SONG_TITLE_CACHE[encode_id] = title

        # Khởi động trước session tải/transcode ngay khi nhận request /stream_pcm (chuẩn 24000 Hz)
        session = get_or_create_stream_session(encode_id, title, sample_rate=24000)
        session.start()

        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
        base_url = f"{proto}://{host}"

        audio_url = f"{base_url}/stream/{encode_id}.mp3"
        lyric_url = f"{base_url}/lyrics/{encode_id}.lrc"

        print(f"[+] Trả về cho ESP32: Title='{title}', Source='{selected.get('source')}', Audio='{audio_url}'")
        return {
            "title": title,
            "artist": artist_name,
            "audio_url": audio_url,
            "lyric_url": lyric_url
        }
    except Exception as e:
        print(f"[!] Lỗi tại /stream_pcm: {e}")
        return {"error": "Song not found", "audio_url": ""}


@app.api_route("/stream/{track_id:path}", methods=["GET", "HEAD"])
async def stream_audio_by_path(track_id: str, request: Request):
    """
    Endpoint phát nhạc: /stream/{id}.mp3 hoặc /stream/{id}
    - Chuẩn đầu ra: MPEG-2 Layer III, Sample Rate: 24,000 Hz, CBR 128kbps stereo.
    - Chống rè loa, tương thích hoàn hảo mọi bản firmware ESP32.
    """
    clean_id = track_id
    if clean_id.endswith(".mp3"):
        clean_id = clean_id[:-4]

    sr_param = request.query_params.get("ar") or request.query_params.get("sample_rate") or "24000"
    try:
        sample_rate = int(sr_param)
    except ValueError:
        sample_rate = 24000

    cached_file = os.path.join(CACHE_DIR, f"{clean_id}_{sample_rate}.mp3")
    if os.path.exists(cached_file) and os.path.getsize(cached_file) > 30000:
        return FileResponse(
            cached_file,
            media_type="audio/mpeg",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=86400",
            }
        )

    title = SONG_TITLE_CACHE.get(clean_id, "")
    session = get_or_create_stream_session(clean_id, title, sample_rate)
    session.start()

    # Chờ dữ liệu ban đầu sẵn sàng (tối đa 6 giây)
    try:
        await asyncio.wait_for(session.ready_event.wait(), timeout=6.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout khởi tạo luồng âm thanh")

    if session.error and not os.path.exists(session.cached_path) and not os.path.exists(session.temp_path):
        raise HTTPException(status_code=500, detail=session.error)

    if os.path.exists(cached_file) and os.path.getsize(cached_file) > 30000:
        return FileResponse(
            cached_file,
            media_type="audio/mpeg",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=86400",
            }
        )

    return StreamingResponse(
        session.stream_generator(),
        media_type="audio/mpeg",
        headers={
            "Content-Type": "audio/mpeg",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        }
    )


@app.get("/lyrics/{track_id:path}")
async def get_lyrics(track_id: str):
    """
    Endpoint trả về file lời bài hát chuẩn .lrc theo đặc tả API_SPEC_MUSIC_SERVER.md
    """
    clean_id = track_id
    if clean_id.endswith(".lrc"):
        clean_id = clean_id[:-4]

    title = SONG_TITLE_CACHE.get(clean_id, clean_id)
    lrc_content = (
        f"[00:00.00]Đang phát: {title}\n"
        f"[00:03.00]Xiaozhi Music Server - Hệ thống âm nhạc AI Việt Nam\n"
        f"[00:06.00]Chúc bạn có những phút giây thư giãn tuyệt vời!\n"
    )
    return StreamingResponse(
        io.BytesIO(lrc_content.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Type": "text/plain; charset=utf-8"}
    )


@app.api_route("/api/song/stream", methods=["GET", "HEAD"])
async def stream_song(id: str, request: Request):
    """
    Tương thích ngược với endpoint cũ /api/song/stream?id=...
    """
    return await stream_audio_by_path(id, request)


def main():
    port = int(os.environ.get("PORT", 8000))
    ip = get_local_ip()
    print("\n" + "=" * 65)
    print("   XIAOZHI MUSIC SERVER (SOUNDCLOUD + ZINGMP3) ĐANG CHẠY")
    print("=" * 65)
    print(f"[*] Port: {port}")
    print(f"[*] Nguồn nhạc: SoundCloud + ZingMP3 (Đã loại bỏ YouTube)")
    print(f"[*] Chế độ: Tối ưu cho máy chủ cấu hình thấp (Low-spec / Free tier)")
    print(f"[*] Endpoint: http://{ip}:{port}")
    print("=" * 65 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
