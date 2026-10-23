import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 【主程序置顶配置区】
# ==============================================================================
RULES_TAB_NAME  = "config"         
SCRIPT_TAB_NAME = os.getenv("TARGET_TAB", "script")        
SHEET_URL       = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"

# 密钥继续从 GitHub Actions 安全环境读取
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
GCP_SA_KEY      = os.getenv("GCP_SA_KEY", "")

TEMP_DIR          = "temp_workspace"
OUTPUT_VIDEO_NAME = "final_output.mp4"

# ==============================================================================
# 🔐 1. Google API 鉴权与下载中心 
# ==============================================================================
def get_google_credentials():
    if not GCP_SA_KEY:
        raise ValueError("❌ 未找到 GCP_SA_KEY 环境变量！")
    info = json.loads(GCP_SA_KEY)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)

def get_google_sheet_tab_csv(sheet_url, tab_name):
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
    """提取纯净的 Google Drive File ID 并下载"""
    match = re.search(r"(?:/file/d/|/d/|id=|folders/)([a-zA-Z0-9-_]+)", str(file_id_or_url))
    file_id = match.group(1) if match else str(file_id_or_url).strip()
    
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
# 🛠️ 2. Google 表格数据解析器（精准对齐对应字段）
# ==============================================================================
def parse_rules_tab(csv_text):
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return {}, {}
    header = [c.strip() for c in rows[0]]
    global_cfg, styles_cfg = {}, {}
    for row in rows[1:]:
        if not row or not row[0].strip(): continue
        obj_name = row[0].strip()
        if "说明" in obj_name or "【" in obj_name: continue
        
        row_dict = {header[i]: row[i].strip() for i in range(1, len(header)) if i < len(row)}
        if obj_name == "全局配置":
            global_cfg = row_dict
        else:
            styles_cfg[obj_name] = row_dict
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
        
        chapter = row_dict.get("章节", "").strip()
        if "说明" in chapter or "【" in chapter or "提示" in chapter: continue
        
        bg_layer0 = row_dict.get("默认素材-layer0", "").strip()
        bg_layer1 = row_dict.get("挂件素材-layer1", "").strip()
        duration_str = row_dict.get("章节秒数", "").strip()
        silent_text = row_dict.get("无声文字", "").strip()
        spoken_text = row_dict.get("有声文字", "").strip()
        
        # 跳过空行
        if not spoken_text and not silent_text and not bg_layer0 and not bg_layer1:
            continue
            
        try:
            duration = float(duration_str) if duration_str else None
        except ValueError:
            duration = None

        clips.append({
            "bg_layer0": bg_layer0,
            "bg_layer1": bg_layer1,
            "duration": duration,
            "chapter": chapter,
            "silent_text": silent_text,
            "spoken_text": spoken_text
        })
    return clips

# ==============================================================================
# 🎬 3. 素材处理与 FFmpeg 渲染引擎
# ==============================================================================
def get_clip_background(clip, style_cfg, global_cfg):
    """优先级：挂件素材-layer1 -> 默认素材-layer0 -> 样式配置 -> 全局配置"""
    asset = (
        clip.get("bg_layer1") or 
        clip.get("bg_layer0") or 
        style_cfg.get("挂件素材-layer1") or 
        style_cfg.get("挂件-layer1") or 
        style_cfg.get("默认素材-layer0") or 
        global_cfg.get("默认素材-layer0", "")
    ).strip()
    
    if not asset:
        return ""
        
    # 1. Drive 链接/ID
    if "drive.google.com" in asset or (len(asset) >= 25 and "/" not in asset and " " not in asset):
        dl_path = f"{TEMP_DIR}/gdrive_{abs(hash(asset))}.mp4"
        if not os.path.exists(dl_path):
            download_file_from_gdrive(asset, dl_path)
        return dl_path
        
    # 2. 本地相对路径
    if os.path.exists(asset):
        return asset
        
    # 3. Pixabay 关键词自动检索
    if PIXABAY_API_KEY and not asset.endswith(".mp4") and not asset.endswith(".png") and not asset.endswith(".jpg"):
        dl_path = f"{TEMP_DIR}/px_{abs(hash(asset))}.mp4"
        if os.path.exists(dl_path):
            return dl_path
        try:
            px_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={urllib.parse.quote(asset)}&per_page=3"
            res = requests.get(px_url, timeout=10).json()
            hits = res.get("hits", [])
            if hits:
                video_url = hits[0]["videos"]["large"]["url"]
                v_res = requests.get(video_url, timeout=30)
                with open(dl_path, "wb") as f:
                    f.write(v_res.content)
                return dl_path
        except Exception as e:
            print(f"⚠️ Pixabay 检索 '{asset}' 失败: {e}")
            
    return ""

async def generate_audio_or_silent(clip, style_cfg, audio_output_path):
    """根据有声文字生成 TTS，无有声文字则按【章节秒数】生成静音轨"""
    spoken_text = clip["spoken_text"]
    
    if spoken_text:
        # 从 config 表读取音色与语速
        voice = style_cfg.get("配音-音色代号", "").strip() or "zh-CN-XiaoxiaoNeural"
        rate_raw = style_cfg.get("配音-语速", "1.0").strip()
        try:
            rate_val = float(rate_raw)
            pct = int((rate_val - 1.0) * 100)
            rate_param = f"+{pct}%" if pct >= 0 else f"{pct}%"
        except Exception:
            rate_param = "+0%"
            
        await edge_tts.Communicate(spoken_text, voice, rate=rate_param).save(audio_output_path)
    else:
        # 无配音时，使用【章节秒数】作为时长，默认 3 秒
        dur = clip["duration"] if clip["duration"] else 3.0
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(dur), "-c:a", "aac", audio_output_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    # 字号与颜色读取
    font_size = style_cfg.get("文字-字号") or global_cfg.get("文字-字号") or "50"
    font_color = style_cfg.get("文字-颜色") or global_cfg.get("文字-颜色") or "white"
    
    # 优先显示【无声文字】标题，若为空则显示【有声文字】
    display_text = clip["silent_text"] if clip["silent_text"] else clip["spoken_text"]
    
    sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
    with open(sub_file, "w", encoding="utf-8") as f:
        f.write(display_text)

    cmd = ["ffmpeg", "-y"]
    bg_path = get_clip_background(clip, style_cfg, global_cfg)
    
    if bg_path and os.path.exists(bg_path):
        cmd.extend(["-stream_loop", "-1", "-i", bg_path]) 
    else:
        cmd.extend(["-f", "lavfi", "-i", "color=c=black:s=1920x1080"])

    cmd.extend(["-i", audio_file])
    
    # 对 FFmpeg 路径进行严格安全转义
    font_path_clean = os.path.abspath(font_path).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
    sub_file_clean = os.path.abspath(sub_file).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")

    filter_chain = (
        f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg];"
        f"[bg]drawtext=fontfile='{font_path_clean}':textfile='{sub_file_clean}':fontcolor={font_color}:fontsize={font_size}:x=(W-w)/2:y=H-text_h-100[outv]"
    )

    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[outv]", "-map", "1:a",
        "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", 
        output_path
    ])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ Clip {index} FFmpeg 错误日志:\n{res.stderr}")
        raise subprocess.CalledProcessError(res.returncode, cmd, output=res.stdout, stderr=res.stderr)

# ==============================================================================
# 🚀 4. 主程序入口
# ==============================================================================
async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    if not clips:
        raise ValueError("❌ 未在表格中解析出任何有效脚本内容，请检查表格。")
    
    # 字体下载
    drive_font_url = global_cfg.get("字体", "").strip()
    if not drive_font_url:
        raise ValueError("❌ 错误：全局配置中未找到【字体】链接！")
        
    font_path = f"{TEMP_DIR}/custom_font.ttf"
    if not os.path.exists(font_path):
        download_file_from_gdrive(drive_font_url, font_path)

    video_segments = []
    
    # 分段渲染
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.m4a"
        
        # 匹配 config 表对应的章节样式（如 片头、男配音、女配音）
        style_cfg = styles_cfg.get(clip["chapter"], {})
        
        # 生成配音或静音轨
        await generate_audio_or_silent(clip, style_cfg, a_seg)
        # 渲染片段
        render_clip(idx, clip, style_cfg, global_cfg, font_path, audio_file=a_seg, output_path=v_seg)
        video_segments.append(v_seg)

    # 拼接校验
    list_file = f"{TEMP_DIR}/concat_list.txt"
    valid_segments = []
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in video_segments:
            if os.path.exists(seg) and os.path.getsize(seg) > 0:
                clean_path = os.path.abspath(seg).replace("'", "'\\''")
                f.write(f"file '{clean_path}'\n")
                valid_segments.append(seg)

    if not valid_segments:
        raise RuntimeError("❌ 没有生成任何有效的视频片段！")

    # FFmpeg 重新编码统一拼接
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        OUTPUT_VIDEO_NAME
    ]
    subprocess.run(concat_cmd, check=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
