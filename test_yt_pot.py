import yt_dlp

url = "https://www.youtube.com/watch?v=boKJ5XDs_mY"

ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,
    'extractor_args': {
        'youtube': {
            'player-client': ['mweb', 'tv', 'web_safari'],
        }
    },
    'js_runtimes': {
        'node': {'path': '/Users/kiwi/.nvm/versions/node/v22.23.3/bin/node'}
    },
    'remote_components': ['ejs:github'],
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    print("\n--- SUCCESS! ---")
    print(f"Title: {info.get('title')}")
    print(f"Duration: {info.get('duration')}s")
    print(f"Stream URL: {info.get('url')[:100]}...")
