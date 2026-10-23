import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 【主程序置顶配置区】
# ==============================================================================
RULES_TAB_NAME    = "config"          # Google 表格中存放样式/全局配置的 Tab 标签页名称
SCRIPT_TAB_NAME   = "script"          # Google 表格中存放台词/分镜脚本的 Tab 标签页名称
DEFAULT_FONT_PATH = "materials/font.ttf" # 默认字体保存/读取路径
OUTPUT_VIDEO_NAME = "final_output.mp4"   # 导出的成品视频文件名

# 从 GitHub Actions 环境变量中安全读取由 Streamlit 传入的数据
SHEET_URL       = os.getenv("SHEET_URL", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
GCP_SA_KEY      = os.getenv("GCP_SA_KEY", "") # 👈 新增：读取我们刚配置好的 Google 密钥

TEMP_DIR = "temp_workspace"


# ==============================================================================
# 🔐 1. Google API 鉴权服务中心 (支持私密表格与私密云端硬盘)
# ==============================================================================

def get_google_credentials():
    """解析服务账号 JSON 密钥，获取访问权限"""
    if not GCP_SA_KEY:
        raise ValueError("❌ 未找到 GCP_SA_KEY 环境变量！请检查 GitHub Secrets 配置。")
    
    info = json.loads(GCP_SA_KEY)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)

def get_google_sheet_tab_csv(sheet_url, tab_name):
    """【已升级为私密 API 版】提取 Google 表格特定 Tab 名称的 CSV 数据"""
    creds = get_google_credentials()
    gc = gspread.authorize(creds)
    
    sh = gc.open_by_url(sheet_url)
    worksheet = sh.worksheet(tab_name)
    records = worksheet.get_all_values()
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(records)
    return output.getvalue()

def download_file_from_gdrive(file_id_or_url, output_path):
    """【新增】通过 API 从私密 Google Drive 静默下载超大视频/素材"""
    match = re.search(r"(?:/d/|id=)([a-zA-Z0-9-_]+)", file_id_or_url)
    file_id = match.group(1) if match else file_id_or_url

    creds = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=creds)

    request = drive_service.files().get_media(fileId=file_id)
    fh = io.FileIO(output_path, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return output_path


# ==============================================================================
# 🛠️ 2. Google 表格数据解析器 (保留了你的原始字段逻辑)
# ==============================================================================

def parse_rules_tab(csv_text):
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return {}, {}
    
    header = [c.strip() for c in rows[0]]
    global_cfg, styles_cfg = {}, {}
    
    for row in rows[1:]:
        if not row or not row[0].strip() or "说明" in row[0]: continue
        obj_name = row[0].strip()
        row_dict = {header[i]: row[i].strip() for i in range(1, len(header)) if i < len(row)}
        if obj_name == "全局配置": global_cfg = row_dict
        else: styles_cfg[obj_name] = row_dict
            
    return global_cfg, styles_cfg

def parse_script_tab(csv_text):
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return []
    
    header = [c.strip() for c in rows[0]]
    clips = []
    
    for row in rows[1:]:
        if not row: continue
        row_dict = {header[i]: row[i].strip() for i in range(len(header)) if i < len(row)}
        
        text = row_dict.get("台词文本", "").strip()
        if not text or text.startswith("#"): continue
        
        clips.append({
            "type": row_dict.get("类型", "speech").strip(),
            "style_tag": row_dict.get("样式标签", "默认正文").strip(),
            "gender": row_dict.get("发音性别", "female").strip(),
            "text": text,
            "bg_asset": row_dict.get("背景指定", "").strip(),
            "keyword": row_dict.get("关键词", "").strip(),
            "overlay_asset": row_dict.get("挂件贴图", "").strip(),
            "is_green": row_dict.get("是否绿幕", "否").strip() in ["是", "TRUE", "1"]
        })
    return clips


# ==============================================================================
# 🔍 3. 素材检索与精准匹配
# ==============================================================================

def search_pixabay_online_bg(keyword):
    if not PIXABAY_API_KEY or not keyword: return ""
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=zh-CN&tl=en&dt=t&q={urllib.parse.quote(keyword)}"
        res = requests.get(url, timeout=3).json()
        en_kw = "".join([item[0] for item in res[0] if item[0]]) or keyword
    except Exception:
        en_kw = keyword

    try:
        v_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={urllib.parse.quote(en_kw)}&orientation=horizontal&per_page=3"
        res_v = requests.get(v_url, timeout=4).json()
        if res_v.get('hits'):
            v_files = res_v['hits'][0].get('videos', {})
            dl_url = v_files.get('large', {}).get('url') or v_files.get('medium', {}).get('url')
            if dl_url:
                dl_path = f"{TEMP_DIR}/pix_v_{res_v['hits'][0]['id']}.mp4"
                if not os.path.exists(dl_path):
                    r = requests.get(dl_url, stream=True)
                    with open(dl_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                return dl_path
    except Exception: pass
    return ""

def get_clip_background(clip_data, global_default_bg):
    # 1. 剧本指定文件 (已升级：自动识别 Google Drive 链接/ID)
    bg_asset = clip_data["bg_asset"]
    if bg_asset:
        if "drive.google.com" in bg_asset or len(bg_asset) == 33: # 识别 Google Drive
            dl_path = f"{TEMP_DIR}/gdrive_bg_{hash(bg_asset)}.mp4"
            if not os.path.exists(dl_path):
                download_file_from_gdrive(bg_asset, dl_path)
            return dl_path
        elif os.path.exists(f"materials/{bg_asset}"): # 识别本地库
            return f"materials/{bg_asset}"
        
    # 2. 剧本显式给出了搜索关键词
    if clip_data["keyword"]:
        online_file = search_pixabay_online_bg(clip_data["keyword"])
        if online_file: return online_file

    # 3. 本地图库名称与台词匹配
    if global_default_bg and os.path.exists(global_default_bg) and os.path.isdir(global_default_bg):
        for f in os.listdir(global_default_bg):
            name, _ = os.path.splitext(f)
            if len(name) > 1 and name in clip_data["text"]:
                return os.path.join(global_default_bg, f)

    # 4. 降级使用网络在线检索台词文本
    if clip_data["text"]:
        online_file = search_pixabay_online_bg(clip_data["text"][:10])
        if online_file: return online_file

    return ""


# ==============================================================================
# 🎬 4. FFmpeg 音视频剪辑合成引擎
# ==============================================================================

async def generate_audio(text, voice_code, speed_val, output_path):
    try:
        val = float(str(speed_val).strip())
        pct = round((val - 1.0) * 100)
        rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
    except Exception: rate_str = "+0%"
    
    await edge_tts.Communicate(text, voice_code, rate=rate_str).save(output_path)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    width, height = 1920, 1080
    fps = global_cfg.get("视频帧率", "30") or "30"
    
    font_size = style_cfg.get("文字字号", "40")
    font_color = style_cfg.get("字体颜色", "white")
    bg_color = style_cfg.get("兜底底色", "black")
    duration = style_cfg.get("停留秒数", "3.0")

    sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
    with open(sub_file, "w", encoding="utf-8") as f: f.write(clip["text"])

    cmd = ["ffmpeg", "-y"]
    
    bg_path = get_clip_background(clip, global_cfg.get("默认素材/图库路径", "materials/bg_lib/"))
    
    if bg_path and os.path.exists(bg_path):
        if bg_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            cmd.extend(["-stream_loop", "-1", "-i", bg_path]) 
        else:
            cmd.extend(["-loop", "1", "-i", bg_path])        
    else:
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}"])

    if audio_file:
        cmd.extend(["-i", audio_file])
    else:
        cmd.extend(["-f", "lavfi", "-i", f"anullsrc=cl=stereo:r=44100:d={duration}"])

    filter_chain = (
        f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}[bg];"
        f"[bg]drawtext=fontfile='{font_path}':textfile='{sub_file}':fontcolor={font_color}:fontsize={font_size}:x=(W-w)/2:y=H-text_h-100[outv]"
    )

    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[outv]", "-map", "1:a",
        "-r", str(fps), "-c:v", "libx264", "-c:a", "aac",
        "-shortest", 
        output_path
    ])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ 镜头 {index} 渲染失败日志:\n{res.stderr}")
        raise RuntimeError(f"FFmpeg 渲染镜头 {index} 失败")


# ==============================================================================
# 🚀 5. 主程序入口
# ==============================================================================

async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    print("▶ [1/4] 🔐 正在通过 Google API 安全读取私密表格...")
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    font_path = DEFAULT_FONT_PATH if os.path.exists(DEFAULT_FONT_PATH) else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    print(f"▶ [2/4] 解析出 {len(clips)} 个分镜镜头，开始自动拉取云端素材并合成...")
    video_segments = []
    
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.mp3"
        style_cfg = styles_cfg.get(clip["style_tag"], {})
        
        if clip["type"] == "speech":
            voice = style_cfg.get("音色代号") or global_cfg.get("女声代码", "zh-CN-XiaoxiaoNeural")
            speed = style_cfg.get("语速倍数") or global_cfg.get("女声语速", "1.2")
            await generate_audio(clip["text"], voice, speed, a_seg)
            render_clip(idx, clip, style_cfg, global_cfg, font_path, audio_file=a_seg, output_path=v_seg)
        else:
            render_clip(idx, clip, style_cfg, global_cfg, font_path, audio_file=None, output_path=v_seg)
            
        video_segments.append(v_seg)

    print("▶ [3/4] 拼接所有分镜镜头...")
    list_file = f"{TEMP_DIR}/concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in video_segments: f.write(f"file '{os.path.abspath(seg)}'\n")
        
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", OUTPUT_VIDEO_NAME], check=True)
    print(f"▶ [4/4] ✨ 视频成功导出，成品路径: {OUTPUT_VIDEO_NAME}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
