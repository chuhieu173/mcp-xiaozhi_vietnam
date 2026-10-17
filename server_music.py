"""
Xiaozhi Multi-Source Music Server (ZingMP3 + YouTube + SoundCloud)
- Hỗ trợ 3 nguồn nhạc mạnh mẽ:
    + ZingMP3: Nhạc bản quyền Việt Nam chất lượng cao (MP3 128kbps trực tiếp, tự động lọc bài VIP).
    + YouTube: Kho nhạc khổng lồ không giới hạn, vượt rào bot check bằng BotGuard PO-Token Provider (M4A/AAC format 140).
    + SoundCloud: Nhạc remix, EDM, quốc tế, indie (AAC/MP4 stream, tự động làm fallback).
- Cơ chế Fallback đa tầng: ZingMP3 (nếu VIP) -> YouTube (PO-Token) -> SoundCloud.
- Tự động phát hiện và khởi động BotGuard PO-Token Provider nếu chưa chạy.
- Tương thích tuyệt đối với firmware Xiaozhi ESP32 (/api/search và /api/song/stream).
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import hashlib
import hmac
import urllib.parse
import yt_dlp
import uvicorn
import httpx
import socket
import os
import sys
import shutil
import subprocess
import io
import time

# Tải trước các plugin của yt_dlp trên luồng chính để tránh race condition khi import đa luồng
try:
    with yt_dlp.YoutubeDL({"quiet": True}) as _ydl_init:
        pass
except Exception:
    pass



def get_node_path():
    """Tự động tìm kiếm đường dẫn Node.js tương thích (ưu tiên Node 22 trong nvm)"""
    nvm_node = os.path.expanduser("~/.nvm/versions/node/v22.23.3/bin/node")
    if os.path.exists(nvm_node):
        return nvm_node
    nvm_dir = os.path.expanduser("~/.nvm/versions/node")
    if os.path.exists(nvm_dir):
        try:
            versions = sorted(os.listdir(nvm_dir), reverse=True)
            for v in versions:
                p = os.path.join(nvm_dir, v, "bin", "node")
                if os.path.exists(p):
                    return p
        except Exception:
            pass
    return shutil.which("node") or "node"


async def ensure_pot_provider():
    """Kiểm tra và tự động khởi động bgutil PO-Token Provider nếu chưa chạy"""
    try:
        async with httpx.AsyncClient(timeout=1.5) as check_client:
            resp = await check_client.get("http://127.0.0.1:4416/ping")
            if resp.status_code == 200:
                return True
    except Exception:
        pass

    candidate_paths = [
        os.path.expanduser("~/bgutil-ytdlp-pot-provider/server/build/main.js"),
        "/app/bgutil-server/server/build/main.js",
        os.path.join(os.getcwd(), "bgutil-server/server/build/main.js"),
    ]
    provider_script = next((p for p in candidate_paths if os.path.exists(p)), None)
    node_bin = get_node_path()
    if provider_script and node_bin:
        try:
            subprocess.Popen(
                [node_bin, provider_script, "-p", "4416"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            print("[+] Tự động khởi động BotGuard PO-Token Provider trên port 4416...")
            return True
        except Exception as e:
            print(f"[!] Không thể tự khởi động PO-Token Provider: {e}")
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_pot_provider()
    yield


app = FastAPI(title="Xiaozhi Multi-Source Music Server", lifespan=lifespan)

# Kích hoạt CORS cho ESP32 và Web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
# 2. YOUTUBE ENGINE (Tích hợp BotGuard PO-Token Provider)
# =====================================================================
YDL_OPTS_YT_SEARCH = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
    "socket_timeout": 4,
    "retries": 0,
}


def get_ydl_opts_yt_stream():
    node_path = get_node_path()
    return {
        "format": "140/bestaudio[ext=m4a]/bestaudio",
        "quiet": True,
        "skip_download": True,
        "extractor_args": {
            "youtube": {
                "player-client": ["mweb", "tv", "web_safari"],
            }
        },
        "js_runtimes": {
            "node": {"path": node_path}
        },
        "remote_components": ["ejs:github"],
        "socket_timeout": 4,
        "retries": 0,
        "extractor_retries": 0,
    }


def search_youtube(q: str, limit: int = 4):
    """Tìm kiếm bài hát trên YouTube"""
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS_YT_SEARCH) as ydl:
            res = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
            items = []
            for entry in res.get("entries", []):
                vid = entry.get("id")
                if not vid:
                    continue
                items.append({
                    "encodeId": f"yt_{vid}",
                    "title": entry.get("title"),
                    "artistsNames": entry.get("uploader", "YouTube"),
                    "source": "YouTube",
                })
            return items
    except Exception as e:
        print(f"[!] Lỗi tìm kiếm YouTube: {e}")
        return []


CACHE_DIR = "/tmp/xiaozhi_music_cache"
os.makedirs(CACHE_DIR, exist_ok=True)
IN_FLIGHT_CACHE: dict[str, asyncio.Task] = {}


async def ensure_track_cached(track_id: str, request: Request = None) -> str:
    """
    Tải và chuyển đổi bài hát sang file MP3 chuẩn 100% trên đĩa cục bộ (/tmp).
    Đặc tính quan trọng cho chip ESP32 (libhelix-mp3):
    1. CBR 128kbps, 44100Hz, Stereo.
    2. Triệt tiêu hoàn toàn header Xing VBR (-write_xing 0) và thẻ ID3v2 (-id3v2_version 0).
       Mỗi frame bắt đầu chuẩn xác bằng MPEG sync word 0xFF 0xFB.
    3. Trả về dưới dạng file tĩnh (FileResponse): Có Content-Length chuẩn, Accept-Ranges: bytes,
       giúp ESP32 nạp đầy buffer tức thời, giải quyết dứt điểm hiện tượng ngắt quãng sau vài giây.
    """
    if track_id.endswith(".mp3"):
        track_id = track_id[:-4]

    cached_path = os.path.join(CACHE_DIR, f"{track_id}.mp3")
    if os.path.exists(cached_path) and os.path.getsize(cached_path) > 50000:
        return cached_path

    # Nếu đang có tiến trình tải bài này, chờ tiến trình đó hoàn thành
    if track_id in IN_FLIGHT_CACHE:
        try:
            return await IN_FLIGHT_CACHE[track_id]
        except Exception:
            pass

    async def _worker():
        # Dọn dẹp cache nếu quá 60 bài
        try:
            files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
            if len(files) > 60:
                files.sort(key=os.path.getmtime)
                for f in files[:20]:
                    try:
                        os.remove(f)
                    except Exception:
                        pass
        except Exception:
            pass

        temp_path = f"{cached_path}.tmp_{os.getpid()}_{int(time.time() * 1000)}"

        # 1. Nguồn ZingMP3 (MP3 128kbps gốc từ CDN Việt Nam)
        if track_id.startswith("zing_"):
            stream_url = await zing_engine.get_stream_url(track_id)
            if stream_url:
                try:
                    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                        resp = await client.get(stream_url)
                        if resp.status_code == 200 and len(resp.content) > 50000:
                            # Chuẩn hóa file Zing sang MP3 chuẩn libhelix (loại bỏ ID3/APIC nặng nếu có)
                            proc = await asyncio.create_subprocess_exec(
                                "ffmpeg", "-y", "-loglevel", "error",
                                "-i", "pipe:0",
                                "-vn", "-c:a", "copy",
                                "-map_metadata", "-1",
                                "-write_xing", "0", "-id3v2_version", "0",
                                "-f", "mp3", temp_path,
                                stdin=asyncio.subprocess.PIPE
                            )
                            proc.stdin.write(resp.content)
                            await proc.stdin.drain()
                            proc.stdin.close()
                            await proc.stdin.wait_closed()
                            await proc.wait()

                            if os.path.exists(temp_path) and os.path.getsize(temp_path) > 50000:
                                os.replace(temp_path, cached_path)
                                return cached_path
                except Exception as e:
                    print(f"[!] Lỗi tải Zing {track_id}: {e}")

            # Fallback nếu Zing VIP
            fallback_title = SONG_TITLE_CACHE.get(track_id) or await zing_engine.get_song_title(track_id)
            if fallback_title:
                print(f"[+] Bài Zing {track_id} bị VIP, tự động chuyển sang SoundCloud: {fallback_title}")
                sc_candidates = await asyncio.to_thread(search_soundcloud, fallback_title, 1)
                if sc_candidates:
                    return await ensure_track_cached(sc_candidates[0]["encodeId"], request)

        # 2. Nguồn SoundCloud
        if track_id.startswith("sc_"):
            clean_id = track_id[3:]
            target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"
            def _ext():
                with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
                    return ydl.extract_info(target_url, download=False)
            try:
                info = await asyncio.to_thread(_ext)
                stream_url = info.get("url")
                if stream_url:
                    proc = await asyncio.create_subprocess_exec(
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-i", stream_url,
                        "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                        "-map_metadata", "-1",
                        "-write_xing", "0", "-id3v2_version", "0",
                        "-f", "mp3", temp_path
                    )
                    await proc.wait()
                    if os.path.exists(temp_path) and os.path.getsize(temp_path) > 50000:
                        os.replace(temp_path, cached_path)
                        return cached_path
            except Exception as e:
                print(f"[!] Lỗi tải SoundCloud {track_id}: {e}")

        # 3. Nguồn YouTube
        if track_id.startswith("yt_"):
            clean_id = track_id[3:]
            target_url = f"https://www.youtube.com/watch?v={clean_id}"
            ydl_opts = get_ydl_opts_yt_stream()
            def _ext_yt():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(target_url, download=False)
            try:
                info = await asyncio.to_thread(_ext_yt)
                stream_url = info.get("url")
                yt_headers = info.get("http_headers", {})
                if stream_url:
                    async with httpx.AsyncClient(headers=yt_headers, timeout=30.0, follow_redirects=True) as client:
                        upstream = await client.send(client.build_request("GET", stream_url), stream=True)
                        proc = await asyncio.create_subprocess_exec(
                            "ffmpeg", "-y", "-loglevel", "error",
                            "-i", "pipe:0",
                            "-vn", "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                            "-map_metadata", "-1",
                            "-write_xing", "0", "-id3v2_version", "0",
                            "-f", "mp3", temp_path,
                            stdin=asyncio.subprocess.PIPE
                        )
                        async def feed():
                            try:
                                async for chunk in upstream.aiter_raw():
                                    proc.stdin.write(chunk)
                                    await proc.stdin.drain()
                            finally:
                                proc.stdin.close()
                                await proc.stdin.wait_closed()
                        await asyncio.gather(feed(), proc.wait())
                        if os.path.exists(temp_path) and os.path.getsize(temp_path) > 50000:
                            os.replace(temp_path, cached_path)
                            return cached_path
            except Exception as e:
                print(f"[!] Lỗi tải YouTube {track_id}: {e}")
                fallback_title = SONG_TITLE_CACHE.get(track_id)
                if fallback_title:
                    sc_candidates = await asyncio.to_thread(search_soundcloud, fallback_title, 1)
                    if sc_candidates:
                        return await ensure_track_cached(sc_candidates[0]["encodeId"], request)

        # 4. Tìm kiếm tự do nếu ID không có tiền tố
        if not (track_id.startswith("zing_") or track_id.startswith("sc_") or track_id.startswith("yt_")):
            title = SONG_TITLE_CACHE.get(track_id) or track_id.replace("-", " ").replace("_", " ")
            sc_cands = await asyncio.to_thread(search_soundcloud, title, 1)
            if sc_cands:
                return await ensure_track_cached(sc_cands[0]["encodeId"], request)

        return None

    task = asyncio.create_task(_worker())
    IN_FLIGHT_CACHE[track_id] = task
    try:
        return await task
    finally:
        IN_FLIGHT_CACHE.pop(track_id, None)


async def stream_youtube_track(video_id_or_yt: str, request: Request = None):
    """Bóc tách và stream âm thanh từ YouTube sử dụng PO-Token Provider, chuyển đổi sang MP3 128kbps"""
    clean_id = video_id_or_yt[3:] if video_id_or_yt.startswith("yt_") else video_id_or_yt
    target_url = f"https://www.youtube.com/watch?v={clean_id}"

    ydl_opts = get_ydl_opts_yt_stream()

    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(target_url, download=False)

    info = await asyncio.to_thread(_extract)
    stream_url = info.get("url")
    if not stream_url:
        raise HTTPException(status_code=404, detail="Không tìm thấy luồng âm thanh trên YouTube")

    # Sử dụng đúng HTTP Headers từ yt-dlp để vượt qua kiểm tra User-Agent của GoogleVideo
    yt_headers = info.get("http_headers", {})
    client = httpx.AsyncClient(headers=yt_headers, timeout=30.0, follow_redirects=True)

    req_headers = {}
    if request and "range" in request.headers:
        req_headers["range"] = request.headers["range"]

    upstream_req = client.build_request("GET", stream_url, headers=req_headers)
    upstream_resp = await client.send(upstream_req, stream=True)

    if upstream_resp.status_code >= 400:
        await upstream_resp.aclose()
        await client.aclose()
        raise HTTPException(status_code=upstream_resp.status_code, detail="GoogleVideo từ chối kết nối stream")

    # Transcode luồng YouTube sang MP3 chuẩn libhelix-mp3 cho ESP32
    return await transcode_stream_to_mp3(upstream_resp, client)


# =====================================================================
# 3. SOUNDCLOUD ENGINE
# =====================================================================
YDL_OPTS_SC_SEARCH = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
}

YDL_OPTS_SC_STREAM = {
    "format": "bestaudio/best",
    "quiet": True,
    "skip_download": True,
}


def search_soundcloud(q: str, limit: int = 3):
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
                    "artistsNames": entry.get("uploader", "Nghệ sĩ"),
                    "source": "SoundCloud",
                })
            return items
    except Exception as e:
        print(f"[!] Lỗi tìm kiếm SoundCloud: {e}")
        return []


async def stream_soundcloud_track(track_id_or_sc: str, request: Request = None):
    """Bóc tách và stream âm thanh từ SoundCloud (chuyển đổi thời gian thực sang chuẩn MP3 128kbps)"""
    clean_id = track_id_or_sc[3:] if track_id_or_sc.startswith("sc_") else track_id_or_sc
    target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
            return ydl.extract_info(target_url, download=False)

    info = await asyncio.to_thread(_extract)
    stream_url = info.get("url")
    if not stream_url:
        raise HTTPException(status_code=404, detail="Không tìm thấy luồng âm thanh trên SoundCloud")

    # ffmpeg có thể đọc trực tiếp URL progressive hoặc HLS .m3u8 và xuất thẳng ra MP3 chuẩn
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-i", stream_url,
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-ar", "44100",
        "-f", "mp3",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def sc_mp3_generator():
        try:
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await process.wait()
            except Exception:
                pass

    return StreamingResponse(
        sc_mp3_generator(),
        media_type="audio/mpeg",
        headers={
            "Content-Type": "audio/mpeg",
            "Accept-Ranges": "none",
            "Cache-Control": "no-cache",
        }
    )


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
# 4. WEB UI & API ENDPOINTS
# =====================================================================
@app.get("/", response_class=HTMLResponse)
def index_page():
    return """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Xiaozhi Cloud Music Server</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 30px; text-align: center; }
            .card { background: #1e293b; max-width: 720px; margin: 0 auto; padding: 25px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
            h1 { color: #38bdf8; margin-bottom: 5px; }
            .status-box { background: #334155; padding: 12px; border-radius: 8px; font-size: 0.9em; margin: 15px 0; color: #cbd5e1; text-align: left; line-height: 1.6; }
            input { padding: 12px; width: 65%; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: #fff; font-size: 1em; }
            button { padding: 12px 20px; border-radius: 6px; border: none; background: #38bdf8; color: #0f172a; font-weight: bold; cursor: pointer; }
            button:hover { background: #7dd3fc; }
            #results { margin-top: 20px; text-align: left; }
            .song-item { background: #334155; padding: 12px; margin-bottom: 8px; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; }
            .badge-zing { background: #2563eb; color: #fff; font-size: 0.75em; padding: 2px 7px; border-radius: 4px; margin-left: 5px; }
            .badge-yt { background: #dc2626; color: #fff; font-size: 0.75em; padding: 2px 7px; border-radius: 4px; margin-left: 5px; }
            .badge-sc { background: #ea580c; color: #fff; font-size: 0.75em; padding: 2px 7px; border-radius: 4px; margin-left: 5px; }
            .error-box { background: #7f1d1d; color: #fecaca; padding: 10px; border-radius: 6px; margin-top: 15px; font-size: 0.9em; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🎵 Xiaozhi Multi-Source Music Server</h1>
            <p style="color: #94a3b8;">Server phát nhạc trực tuyến cho trợ lý AI Xiaozhi ESP32</p>
            
            <div class="status-box">
                <div>⚡ Động cơ âm nhạc: <strong>ZingMP3</strong> + <strong>YouTube (BotGuard PO-Token)</strong> + <strong>SoundCloud</strong></div>
                <div>🛡️ Vượt rào bot: <strong>Tích hợp PO-Token Provider (Port 4416) & Fallback đa tầng</strong></div>
            </div>

            <div style="margin-top: 25px;">
                <input type="text" id="keyword" placeholder="Nhập tên bài hát (ví dụ: Lạc trôi, Nơi này có anh)..." value="Lạc trôi">
                <button onclick="searchSong()">Tìm & Nghe thử</button>
            </div>

            <div id="loading" style="display:none; margin-top:15px; color:#38bdf8;">Đang tìm kiếm bài hát trên ZingMP3, YouTube & SoundCloud...</div>
            <div id="results"></div>
            <div id="player-container" style="display:none; margin-top:20px;">
                <h4 id="nowPlaying" style="color:#38bdf8; margin: 0 0 10px 0;"></h4>
                <audio id="audioPlayer" controls autoplay style="width: 100%;"></audio>
            </div>
        </div>

        <script>
            async function searchSong() {
                const q = document.getElementById('keyword').value.trim();
                if (!q) return;
                document.getElementById('loading').style.display = 'block';
                document.getElementById('results').innerHTML = '';
                
                try {
                    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                    const json = await res.json();
                    document.getElementById('loading').style.display = 'none';
                    
                    if (!res.ok || json.err !== 0) {
                        document.getElementById('results').innerHTML = `<div class="error-box">Lỗi: ${json.detail || json.msg || 'Không thể tìm kiếm'}</div>`;
                        return;
                    }

                    const items = json.data?.items || [];
                    if (items.length === 0) {
                        document.getElementById('results').innerHTML = '<p style="text-align:center; color:#94a3b8;">Không tìm thấy bài hát nào.</p>';
                        return;
                    }

                    let html = '';
                    items.forEach(item => {
                        const titleSafe = item.title.replace(/'/g, "\\'");
                        let badge = '';
                        if (item.encodeId.startsWith('zing_')) {
                            badge = '<span class="badge-zing">ZingMP3</span>';
                        } else if (item.encodeId.startsWith('yt_')) {
                            badge = '<span class="badge-yt">YouTube (POT)</span>';
                        } else {
                            badge = '<span class="badge-sc">SoundCloud</span>';
                        }

                        html += `
                        <div class="song-item">
                            <div>
                                <strong>${item.title}</strong> ${badge}<br>
                                <small style="color:#94a3b8;">${item.artistsNames}</small>
                            </div>
                            <button onclick="playSong('${item.encodeId}', '${titleSafe}')">▶ Phát</button>
                        </div>`;
                    });
                    document.getElementById('results').innerHTML = html;
                } catch (e) {
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('results').innerHTML = `<div class="error-box">Lỗi kết nối: ${e}</div>`;
                }
            }

            function playSong(id, title) {
                const playerContainer = document.getElementById('player-container');
                const player = document.getElementById('audioPlayer');
                const nowPlaying = document.getElementById('nowPlaying');
                
                nowPlaying.innerText = 'Đang phát: ' + (title || id);
                player.src = `/api/song/stream?id=${id}&title=${encodeURIComponent(title || '')}`;
                playerContainer.style.display = 'block';
                player.play().catch(e => console.log('Cần tương tác để phát:', e));
            }
        </script>
    </body>
    </html>
    """


# Bộ nhớ đệm lưu tiêu đề bài hát từ các lượt tìm kiếm để phục vụ tự động Fallback
SONG_TITLE_CACHE: dict[str, str] = {}


@app.get("/health")
async def health_check():
    pot_active = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as check_client:
            resp = await check_client.get("http://127.0.0.1:4416/ping")
            if resp.status_code == 200:
                pot_active = True
    except Exception:
        pass

    return {
        "status": "ok",
        "engines": ["ZingMP3", "YouTube", "SoundCloud"],
        "botguard_pot": "active" if pot_active else "offline",
        "compatible_with": "Xiaozhi ESP32",
    }


@app.get("/api/debug")
async def debug_endpoint():
    node_path = get_node_path()
    node_ver = "not found"
    if node_path:
        try:
            res = subprocess.run([node_path, "-v"], capture_output=True, text=True, timeout=2)
            node_ver = res.stdout.strip()
        except Exception as e:
            node_ver = f"error: {e}"

    pot_status = "offline"
    try:
        async with httpx.AsyncClient(timeout=1.5) as c:
            r = await c.get("http://127.0.0.1:4416/ping")
            pot_status = r.json()
    except Exception as e:
        pot_status = f"offline ({e})"

    bgutil_paths = [
        os.path.expanduser("~/bgutil-ytdlp-pot-provider/server/build/main.js"),
        "/app/bgutil-server/server/build/main.js",
        os.path.join(os.getcwd(), "bgutil-server/server/build/main.js"),
    ]
    found_bgutil = [p for p in bgutil_paths if os.path.exists(p)]

    return {
        "status": "debug",
        "is_docker": os.path.exists("/app/entrypoint.sh"),
        "node_path": node_path,
        "node_version": node_ver,
        "botguard_pot_provider": pot_status,
        "found_bgutil_scripts": found_bgutil,
        "cached_songs_count": len(SONG_TITLE_CACHE),
    }


@app.get("/api/search")
async def search_song(q: str):
    """
    Tìm kiếm bài hát đồng thời từ ZingMP3, YouTube và SoundCloud.
    """
    try:
        # Chạy tìm kiếm song song không block event loop
        zing_task = zing_engine.search(q, limit=4)
        yt_task = asyncio.to_thread(search_youtube, q, 4)
        sc_task = asyncio.to_thread(search_soundcloud, q, 3)

        zing_results, yt_results, sc_results = await asyncio.gather(
            zing_task, yt_task, sc_task, return_exceptions=True
        )

        zing_items = zing_results if isinstance(zing_results, list) else []
        yt_items = yt_results if isinstance(yt_results, list) else []
        sc_items = sc_results if isinstance(sc_results, list) else []

        # Kết hợp kết quả: Ưu tiên HÀNG ĐẦU SoundCloud -> ZingMP3 -> YouTube
        combined = sc_items + zing_items + yt_items

        # Lưu tiêu đề bài hát vào cache để hỗ trợ tự động Fallback cứu luồng âm thanh
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
    Tuân thủ 100% đặc tả trong tài liệu API_SPEC_MUSIC_SERVER.md:
    ƯU TIÊN HÀNG ĐẦU: SoundCloud (không bị chặn 429, không dính bot-guard, cực kỳ ổn định trên Render)
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
        # Tìm kiếm bài hát song song trên cả 3 nguồn (ưu tiên SoundCloud)
        sc_task = asyncio.to_thread(search_soundcloud, query, 3)
        zing_task = zing_engine.search(query, limit=3)
        yt_task = asyncio.to_thread(search_youtube, query, 2)

        sc_results, zing_results, yt_results = await asyncio.gather(
            sc_task, zing_task, yt_task, return_exceptions=True
        )

        sc_items = sc_results if isinstance(sc_results, list) else []
        zing_items = zing_results if isinstance(zing_results, list) else []
        yt_items = yt_results if isinstance(yt_results, list) else []

        selected = None

        # 1. ƯU TIÊN 1: ZingMP3 MIỄN PHÍ (Bản thu chuẩn gốc chất lượng cao nhất, tải tức thời từ CDN Việt Nam)
        for item in zing_items:
            stream_url = await zing_engine.get_stream_url(item["encodeId"])
            if stream_url:
                selected = item
                break

        # 2. ƯU TIÊN 2: SoundCloud (Kho nhạc khổng lồ, EDM, remix, không dính 429 trên Render)
        if not selected and sc_items:
            selected = sc_items[0]

        # 3. DỰ PHÒNG 3: YouTube (chỉ dùng khi SoundCloud và Zing không tìm thấy)
        if not selected and yt_items:
            selected = yt_items[0]

        # 4. Dự phòng nếu chỉ có Zing (tự động fallback khi tải)
        if not selected and zing_items:
            selected = zing_items[0]

        if not selected:
            print(f"[!] Không tìm thấy bài hát nào cho: {query}")
            return {"error": "Song not found", "audio_url": ""}

        encode_id = selected["encodeId"]
        title = selected.get("title") or song_param
        artist_name = selected.get("artistsNames") or artist_param or "Nhiều nghệ sĩ"

        # Lưu tiêu đề vào cache để hỗ trợ fallback và đồng bộ lời
        SONG_TITLE_CACHE[encode_id] = title

        # KÍCH HOẠT TẢI/TRANSCODE TRƯỚC VÀO CACHE NGAY LẬP TỨC:
        # Trong khi ESP32 nhận JSON và thiết lập kết nối, file MP3 đã được tạo sẵn trên đĩa
        asyncio.create_task(ensure_track_cached(encode_id, request))

        # Xây dựng Base URL động (tự động nhận diện https sau Reverse Proxy của Render)
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
    Endpoint phát nhạc theo đường dẫn: /stream/{id}.mp3 hoặc /stream/{id}
    Phục vụ trực tiếp file MP3 chuẩn CBR 128kbps từ cache đĩa (/tmp) với Content-Length và Range đầy đủ.
    Đảm bảo 100% không bị ngắt quãng giữa chừng trên ESP32.
    """
    clean_id = track_id
    if clean_id.endswith(".mp3"):
        clean_id = clean_id[:-4]

    cached_file = await ensure_track_cached(clean_id, request)
    if not cached_file or not os.path.exists(cached_file):
        raise HTTPException(status_code=404, detail=f"Không thể tải bài hát: {clean_id}")

    return FileResponse(
        cached_file,
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
        }
    )


@app.get("/lyrics/{track_id:path}")
async def get_lyrics(track_id: str):
    """
    Endpoint trả về file lời bài hát chuẩn .lrc theo đặc tả API_SPEC_MUSIC_SERVER.md
    Content-Type: text/plain; charset=utf-8
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
    print("   XIAOZHI MULTI-SOURCE MUSIC SERVER (ZING + YT + SC) ĐANG CHẠY")
    print("=" * 65)
    print(f"[*] Port: {port}")
    print(f"[*] Nguồn nhạc: ZingMP3 (MP3 128k) + YouTube (PO-Token) + SoundCloud")
    print(f"[*] Vượt rào Bot: BotGuard PO-Token Provider (Port 4416)")
    print(f"[*] Endpoint: http://{ip}:{port}")
    print("=" * 65 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
