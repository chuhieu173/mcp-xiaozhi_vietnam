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
from fastapi.responses import StreamingResponse, HTMLResponse
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


zing_engine = ZingMP3Engine()


# =====================================================================
# 2. YOUTUBE ENGINE (Tích hợp BotGuard PO-Token Provider)
# =====================================================================
YDL_OPTS_YT_SEARCH = {
    "quiet": True,
    "skip_download": True,
    "extract_flat": True,
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


async def stream_youtube_track(video_id_or_yt: str, request: Request = None):
    """Bóc tách và stream âm thanh từ YouTube sử dụng PO-Token Provider (M4A/AAC 128kbps)"""
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

    async def yt_stream_generator():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    content_type = upstream_resp.headers.get("content-type", "audio/mp4")
    response_headers = {
        "Content-Type": content_type,
        "Accept-Ranges": "bytes",
    }
    if "content-length" in upstream_resp.headers:
        response_headers["Content-Length"] = upstream_resp.headers["content-length"]
    if "content-range" in upstream_resp.headers:
        response_headers["Content-Range"] = upstream_resp.headers["content-range"]

    return StreamingResponse(
        yt_stream_generator(),
        status_code=upstream_resp.status_code,
        headers=response_headers,
    )


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
    """Bóc tách và stream âm thanh từ SoundCloud"""
    clean_id = track_id_or_sc[3:] if track_id_or_sc.startswith("sc_") else track_id_or_sc
    target_url = f"https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A{clean_id}"

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTS_SC_STREAM) as ydl:
            return ydl.extract_info(target_url, download=False)

    info = await asyncio.to_thread(_extract)
    stream_url = info.get("url")
    if not stream_url:
        raise HTTPException(status_code=404, detail="Không tìm thấy luồng âm thanh trên SoundCloud")

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

    if ".m3u8" in stream_url:
        playlist_resp = await client.get(stream_url)
        lines = playlist_resp.text.splitlines()

        init_url = None
        segment_urls = []
        for line in lines:
            line = line.strip()
            if line.startswith("#EXT-X-MAP:URI="):
                init_url = line.split('URI="')[1].rstrip('"')
            elif line and not line.startswith("#"):
                segment_urls.append(line)

        async def sc_hls_generator():
            try:
                if init_url:
                    init_res = await client.get(init_url)
                    yield init_res.content
                for seg in segment_urls:
                    seg_res = await client.get(seg)
                    yield seg_res.content
            finally:
                await client.aclose()

        return StreamingResponse(
            sc_hls_generator(),
            media_type="audio/mp4",
            headers={
                "Content-Type": "audio/mp4",
                "Accept-Ranges": "none",
                "Cache-Control": "no-cache",
            }
        )

    # Nếu là file progressive trực tiếp
    req_headers = {}
    if request and "range" in request.headers:
        req_headers["range"] = request.headers["range"]

    upstream_req = client.build_request("GET", stream_url, headers=req_headers)
    upstream_resp = await client.send(upstream_req, stream=True)

    async def sc_direct_generator():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    response_headers = {
        "Content-Type": upstream_resp.headers.get("content-type", "audio/mp4"),
        "Accept-Ranges": "bytes",
    }
    if "content-length" in upstream_resp.headers:
        response_headers["Content-Length"] = upstream_resp.headers["content-length"]
    if "content-range" in upstream_resp.headers:
        response_headers["Content-Range"] = upstream_resp.headers["content-range"]

    return StreamingResponse(
        sc_direct_generator(),
        status_code=upstream_resp.status_code,
        headers=response_headers,
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


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "engines": ["ZingMP3", "YouTube", "SoundCloud"],
        "botguard_pot": "active",
        "compatible_with": "Xiaozhi ESP32",
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
        sc_task = asyncio.to_thread(search_soundcloud, q, 2)

        zing_results, yt_results, sc_results = await asyncio.gather(
            zing_task, yt_task, sc_task, return_exceptions=True
        )

        zing_items = zing_results if isinstance(zing_results, list) else []
        yt_items = yt_results if isinstance(yt_results, list) else []
        sc_items = sc_results if isinstance(sc_results, list) else []

        # Kết hợp kết quả: Ưu tiên ZingMP3 -> YouTube -> SoundCloud
        combined = zing_items + yt_items + sc_items

        return {
            "err": 0,
            "msg": "Success",
            "data": {"items": combined}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi tìm kiếm: {str(e)}")


@app.api_route("/api/song/stream", methods=["GET", "HEAD"])
async def stream_song(id: str, request: Request):
    """
    Stream âm thanh trực tiếp về ESP32 hoặc Web Browser.
    Hỗ trợ phát từ ZingMP3 (MP3 128kbps), YouTube (M4A/AAC có PO-Token) và SoundCloud (fMP4/AAC).
    Tự động Fallback đa tầng: ZingMP3 (nếu VIP) -> YouTube -> SoundCloud.
    """
    fallback_title = request.query_params.get("title")

    try:
        # ==============================================================
        # TRƯỜNG HỢP 1: BÀI HÁT TỪ YOUTUBE
        # ==============================================================
        if id.startswith("yt_"):
            try:
                return await stream_youtube_track(id, request)
            except Exception as e:
                print(f"[!] Lỗi stream YouTube {id}: {e}")
                if fallback_title:
                    print(f"[+] Fallback YouTube sang SoundCloud cho: {fallback_title}")
                    sc_candidates = await asyncio.to_thread(search_soundcloud, fallback_title, 1)
                    if sc_candidates:
                        return await stream_soundcloud_track(sc_candidates[0]["encodeId"], request)
                raise HTTPException(status_code=500, detail=f"Lỗi phát nhạc YouTube: {e}")

        # ==============================================================
        # TRƯỜNG HỢP 2: BÀI HÁT TỪ ZINGMP3
        # ==============================================================
        if id.startswith("zing_"):
            audio_url = await zing_engine.get_stream_url(id)

            if audio_url:
                client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
                req_headers = {}
                if "range" in request.headers:
                    req_headers["range"] = request.headers["range"]

                upstream_req = client.build_request("GET", audio_url, headers=req_headers)
                upstream_resp = await client.send(upstream_req, stream=True)

                async def zing_stream_generator():
                    try:
                        async for chunk in upstream_resp.aiter_raw():
                            yield chunk
                    finally:
                        await upstream_resp.aclose()
                        await client.aclose()

                response_headers = {
                    "Content-Type": upstream_resp.headers.get("content-type", "audio/mpeg"),
                    "Accept-Ranges": "bytes",
                }
                if "content-length" in upstream_resp.headers:
                    response_headers["Content-Length"] = upstream_resp.headers["content-length"]
                if "content-range" in upstream_resp.headers:
                    response_headers["Content-Range"] = upstream_resp.headers["content-range"]

                return StreamingResponse(
                    zing_stream_generator(),
                    status_code=upstream_resp.status_code,
                    headers=response_headers,
                )

            # Nếu Zing VIP -> Fallback sang YouTube trước, sau đó SoundCloud
            if fallback_title:
                print(f"[+] Bài Zing {id} là VIP, tự động fallback sang YouTube: {fallback_title}")
                yt_candidates = await asyncio.to_thread(search_youtube, fallback_title, 1)
                if yt_candidates:
                    try:
                        return await stream_youtube_track(yt_candidates[0]["encodeId"], request)
                    except Exception as yt_err:
                        print(f"[!] Fallback YouTube thất bại: {yt_err}")

                print(f"[+] Chuyển tiếp fallback sang SoundCloud: {fallback_title}")
                sc_candidates = await asyncio.to_thread(search_soundcloud, fallback_title, 1)
                if sc_candidates:
                    return await stream_soundcloud_track(sc_candidates[0]["encodeId"], request)

            raise HTTPException(
                status_code=403,
                detail="Bài hát này yêu cầu Zing VIP và không tìm thấy bản thay thế miễn phí trên YouTube / SoundCloud."
            )

        # ==============================================================
        # TRƯỜNG HỢP 3: BÀI HÁT TỪ SOUNDCLOUD
        # ==============================================================
        return await stream_soundcloud_track(id, request)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi phát nhạc: {str(e)}")


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
