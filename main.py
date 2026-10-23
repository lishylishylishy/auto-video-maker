import os, re, csv, json, io, requests, subprocess, edge_tts, urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==============================================================================
# ⚙️ 【主程序置顶配置区】
# ==============================================================================
RULES_TAB_NAME    = "config"          
SCRIPT_TAB_NAME   = "script"          
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit?gid=0#gid=0"# 👇 你的私密表格链接死编码写在这里！程序直接读这个，不需要到处传参了

# 密钥继续从 GitHub Actions 安全环境读取
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")
GCP_SA_KEY      = os.getenv("GCP_SA_KEY", "")

TEMP_DIR = "temp_workspace"
OUTPUT_VIDEO_NAME = "final_output.mp4"

# ==============================================================================
# 🔐 1. Google API 鉴权服务中心 
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
# 🛠️ 2. Google 表格数据解析器
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
            "text": text,
            "bg_asset": row_dict.get("背景指定", "").strip()
        })
    return clips

# ==============================================================================
# 🎬 3. 素材拉取与 FFmpeg 合成
# ==============================================================================
def get_clip_background(clip_data):
    bg_asset = clip_data["bg_asset"]
    if bg_asset and ("drive.google.com" in bg_asset or len(bg_asset) >= 33):
        dl_path = f"{TEMP_DIR}/gdrive_bg_{hash(bg_asset)}.mp4"
        if not os.path.exists(dl_path):
            download_file_from_gdrive(bg_asset, dl_path)
        return dl_path
    return ""

async def generate_audio(text, output_path):
    await edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural", rate="+10%").save(output_path)

def render_clip(index, clip, style_cfg, font_path, audio_file, output_path):
    font_size = style_cfg.get("文字字号", "40")
    font_color = style_cfg.get("字体颜色", "white")
    
    sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
    with open(sub_file, "w", encoding="utf-8") as f: f.write(clip["text"])

    cmd = ["ffmpeg", "-y"]
    bg_path = get_clip_background(clip)
    
    if bg_path and os.path.exists(bg_path):
        cmd.extend(["-stream_loop", "-1", "-i", bg_path]) 
    else:
        cmd.extend(["-f", "lavfi", "-i", "color=c=black:s=1920x1080"])

    cmd.extend(["-i", audio_file])
    
    filter_chain = (
        f"[0:v]setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg];"
        f"[bg]drawtext=fontfile='{font_path}':textfile='{sub_file}':fontcolor={font_color}:fontsize={font_size}:x=(W-w)/2:y=H-text_h-100[outv]"
    )

    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[outv]", "-map", "1:a",
        "-r", "30", "-c:v", "libx264", "-c:a", "aac",
        "-shortest", 
        output_path
    ])
    
    subprocess.run(cmd, capture_output=True, text=True, check=True)

# ==============================================================================
# 🚀 4. 主程序入口
# ==============================================================================
async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    # 强制要求表格中必须填写 Drive 字体链接
    drive_font_url = global_cfg.get("全局字体素材", "").strip()
    if not drive_font_url:
        raise ValueError("❌ 错误：你的 config 表格中没有填写【全局字体素材】的 Google Drive 链接！程序无法渲染中文字幕。")
        
    font_path = f"{TEMP_DIR}/custom_font.ttf"
    if not os.path.exists(font_path):
        download_file_from_gdrive(drive_font_url, font_path)

    video_segments = []
    
    for idx, clip in enumerate(clips, 1):
        v_seg = f"{TEMP_DIR}/clip_{idx}.mp4"
        a_seg = f"{TEMP_DIR}/clip_{idx}.mp3"
        style_cfg = styles_cfg.get(clip["style_tag"], {})
        
        await generate_audio(clip["text"], a_seg)
        render_clip(idx, clip, style_cfg, font_path, audio_file=a_seg, output_path=v_seg)
        video_segments.append(v_seg)

    list_file = f"{TEMP_DIR}/concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for seg in video_segments: f.write(f"file '{os.path.abspath(seg)}'\n")
        
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", OUTPUT_VIDEO_NAME], check=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
