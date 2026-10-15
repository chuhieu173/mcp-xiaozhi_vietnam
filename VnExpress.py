from mcp.server.fastmcp import FastMCP
import sys
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import re

logger = logging.getLogger('VnExpress')

# Fix UTF-8 encoding for Windows console
if sys.platform == 'win32':
    sys.stderr.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')

# Create an MCP server
mcp = FastMCP("VnExpress")

# Danh mục RSS chính thức của VnExpress
RSS_FEEDS = {
    "home": "https://vnexpress.net/rss/tin-moi-nhat.rss",
    "thoi-su": "https://vnexpress.net/rss/thoi-su.rss",
    "the-gioi": "https://vnexpress.net/rss/the-gioi.rss",
    "kinh-doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
    "bat-dong-san": "https://vnexpress.net/rss/bat-dong-san.rss",
    "khoa-hoc": "https://vnexpress.net/rss/khoa-hoc.rss",
    "giai-tri": "https://vnexpress.net/rss/giai-tri.rss",
    "the-thao": "https://vnexpress.net/rss/the-thao.rss",
    "phap-luat": "https://vnexpress.net/rss/phap-luat.rss",
    "giao-duc": "https://vnexpress.net/rss/giao-duc.rss",
    "suc-khoe": "https://vnexpress.net/rss/suc-khoe.rss",
    "doi-song": "https://vnexpress.net/rss/doi-song.rss",
    "du-lich": "https://vnexpress.net/rss/du-lich.rss",
    "so-hoa": "https://vnexpress.net/rss/so-hoa.rss",
    "xe": "https://vnexpress.net/rss/oto-xe-may.rss",
    "goc-nhin": "https://vnexpress.net/rss/y-kien.rss",
}

@mcp.tool()
def get_vnexpress_news(category: str = "home", limit: int = 10) -> dict:
    """
    Lấy tin tức mới nhất từ VnExpress.
    
    Categories:
    - home: Trang chủ (tin nổi bật)
    - thoi-su: Thời sự
    - goc-nhin: Góc nhìn  
    - the-gioi: Thế giới
    - kinh-doanh: Kinh doanh
    - bat-dong-san: Bất động sản
    - khoa-hoc: Khoa học
    - giai-tri: Giải trí
    - the-thao: Thể thao
    - phap-luat: Pháp luật
    - giao-duc: Giáo dục
    - suc-khoe: Sức khỏe
    - doi-song: Đời sống
    - du-lich: Du lịch
    - so-hoa: Số hóa
    - xe: Xe
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'vi-VN,vi;q=0.9,en;q=0.8',
        }

        # Ưu tiên lấy tin qua RSS Feed (cực kỳ nhanh, ổn định và đầy đủ tóm tắt)
        rss_url = RSS_FEEDS.get(category.lower().strip())
        if rss_url:
            try:
                logger.info(f"Fetching RSS feed: {rss_url}")
                rss_resp = requests.get(rss_url, headers=headers, timeout=10)
                if rss_resp.status_code == 200:
                    rss_soup = BeautifulSoup(rss_resp.content, "xml")
                    items = rss_soup.find_all("item")
                    if items:
                        articles = []
                        for it in items[:limit]:
                            t_elem = it.find("title")
                            l_elem = it.find("link")
                            d_elem = it.find("description")
                            p_elem = it.find("pubDate")
                            
                            title = t_elem.get_text(strip=True) if t_elem else ""
                            link = l_elem.get_text(strip=True) if l_elem else ""
                            raw_desc = d_elem.get_text(strip=True) if d_elem else ""
                            clean_desc = re.sub(r'<[^>]+>', '', raw_desc).strip()
                            pub_date = p_elem.get_text(strip=True) if p_elem else ""
                            
                            if title and link:
                                articles.append({
                                    "title": title,
                                    "url": link,
                                    "description": clean_desc,
                                    "time": pub_date,
                                    "category": category
                                })
                        if articles:
                            logger.info(f"Successfully fetched {len(articles)} articles from RSS")
                            return {
                                "success": True,
                                "category": category,
                                "total_articles": len(articles),
                                "articles": articles,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "source": "VnExpress.net (RSS)"
                            }
            except Exception as e:
                logger.warning(f"Lỗi khi đọc RSS, chuyển sang HTML scraping: {e}")

        # Fallback: HTML Scraping
        if category == "home":
            url = "https://vnexpress.net/"
        else:
            url = f"https://vnexpress.net/{category}"
        
        logger.info(f"Fetching news from HTML: {url}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        # Parse HTML
        soup = BeautifulSoup(response.content, 'html.parser')
        
        articles = []
        
        # Tìm các bài viết (selector có thể thay đổi theo cấu trúc VnExpress)
        # Thử nhiều selector khác nhau
        selectors = [
            'article.item-news',
            '.item-news',
            '.title-news a',
            'h3.title-news a',
            '.item-news .title-news a'
        ]
        
        found_articles = []
        for selector in selectors:
            found_articles = soup.select(selector)
            if found_articles:
                logger.info(f"Found {len(found_articles)} articles with selector: {selector}")
                break
        
        if not found_articles:
            # Fallback: tìm tất cả links có chứa từ khóa tin tức
            found_articles = soup.find_all('a', href=re.compile(r'\.html$'))
            logger.info(f"Fallback: Found {len(found_articles)} potential article links")
        
        count = 0
        for item in found_articles:
            if count >= limit:
                break
                
            try:
                # Xử lý khác nhau dựa trên cấu trúc element
                if item.name == 'a':
                    link_elem = item
                    title = item.get_text(strip=True)
                else:
                    link_elem = item.find('a')
                    title_elem = item.find(['h1', 'h2', 'h3', '.title-news'])
                    title = title_elem.get_text(strip=True) if title_elem else link_elem.get_text(strip=True)
                
                if not link_elem or not title:
                    continue
                
                href = link_elem.get('href')
                if not href:
                    continue
                
                # Tạo URL đầy đủ
                if href.startswith('/'):
                    full_url = f"https://vnexpress.net{href}"
                elif href.startswith('http'):
                    full_url = href
                else:
                    full_url = f"https://vnexpress.net/{href}"
                
                # Lấy thêm thông tin nếu có
                description = ""
                time_str = ""
                
                # Tìm description
                desc_elem = None
                if item.name != 'a':
                    desc_elem = item.find(['p', '.description', '.lead'])
                if desc_elem:
                    description = desc_elem.get_text(strip=True)
                
                # Tìm thời gian
                time_elem = None
                if item.name != 'a':
                    time_elem = item.find(['time', '.time'])
                if time_elem:
                    time_str = time_elem.get_text(strip=True)
                
                # Lọc bỏ những bài không phải tin tức chính
                if len(title) < 10 or 'javascript:' in href:
                    continue
                
                article = {
                    "title": title,
                    "url": full_url,
                    "description": description,
                    "time": time_str,
                    "category": category
                }
                
                articles.append(article)
                count += 1
                
            except Exception as e:
                logger.warning(f"Error processing article: {e}")
                continue
        
        logger.info(f"Successfully extracted {len(articles)} articles from {category}")
        
        return {
            "success": True,
            "category": category,
            "total_articles": len(articles),
            "articles": articles,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "VnExpress.net"
        }
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error: {e}")
        return {"success": False, "error": f"Network error: {str(e)}"}
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {"success": False, "error": f"Unexpected error: {str(e)}"}

@mcp.tool()
def get_article_content(url: str) -> dict:
    """Lấy nội dung chi tiết của một bài báo từ URL VnExpress"""
    try:
        logger.info(f"Fetching article content from: {url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Tìm tiêu đề
        title_elem = soup.find(['h1', '.title-detail'])
        title = title_elem.get_text(strip=True) if title_elem else "Không tìm thấy tiêu đề"
        
        # Tìm mô tả/lead
        description_elem = soup.find(['.description', '.lead'])
        description = description_elem.get_text(strip=True) if description_elem else ""
        
        # Tìm nội dung chính
        content_elem = soup.find(['.fck_detail', '.content-detail', '.Normal'])
        content = ""
        if content_elem:
            # Lấy text từ các paragraph
            paragraphs = content_elem.find_all('p')
            content = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
        # Tìm thời gian
        time_elem = soup.find(['time', '.date'])
        publish_time = time_elem.get_text(strip=True) if time_elem else ""
        
        # Tìm tác giả
        author_elem = soup.find(['.author', '.Normal b'])
        author = author_elem.get_text(strip=True) if author_elem else ""
        
        return {
            "success": True,
            "title": title,
            "description": description,
            "content": content[:2000] + "..." if len(content) > 2000 else content,  # Giới hạn độ dài
            "author": author,
            "publish_time": publish_time,
            "url": url,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
    except Exception as e:
        logger.error(f"Error fetching article content: {e}")
        return {"success": False, "error": str(e)}

@mcp.tool() 
def search_vnexpress_news(keyword: str, limit: int = 5) -> dict:
    """Tìm kiếm tin tức trên VnExpress theo từ khóa"""
    try:
        # URL tìm kiếm VnExpress
        search_url = f"https://timkiem.vnexpress.net/?q={keyword}"
        
        logger.info(f"Searching VnExpress for: {keyword}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(search_url, headers=headers, timeout=15)
        response.raise_for_status()
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        articles = []
        # Tìm kết quả tìm kiếm
        search_results = soup.find_all(['article', '.item-news'], limit=limit)
        
        for item in search_results:
            try:
                link_elem = item.find('a')
                if not link_elem:
                    continue
                    
                title = link_elem.get_text(strip=True)
                href = link_elem.get('href')
                
                if href and not href.startswith('http'):
                    href = f"https://vnexpress.net{href}"
                
                description_elem = item.find(['p', '.description'])
                description = description_elem.get_text(strip=True) if description_elem else ""
                
                articles.append({
                    "title": title,
                    "url": href,
                    "description": description,
                    "keyword": keyword
                })
                
            except Exception as e:
                continue
        
        return {
            "success": True,
            "keyword": keyword,
            "total_results": len(articles),
            "articles": articles,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        return {"success": False, "error": str(e)}

# Start the server
if __name__ == "__main__":
    mcp.run(transport="stdio")