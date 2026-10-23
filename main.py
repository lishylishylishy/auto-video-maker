import os, re, csv, requests, subprocess, edge_tts, urllib.parse

# ==============================================================================
# ⚙️ 【主程序置顶配置区】(所有可能调整的参数集中在此处，下方逻辑无需改动)
# ==============================================================================
RULES_TAB_NAME    = "映射规则"          # Google 表格中存放样式/全局配置的 Tab 标签页名称
SCRIPT_TAB_NAME   = "剧本脚本"          # Google 表格中存放台词/分镜脚本的 Tab 标签页名称
DEFAULT_FONT_PATH = "materials/font.ttf" # 默认字体保存/读取路径
OUTPUT_VIDEO_NAME = "final_output.mp4"   # 导出的成品视频文件名

# 从 GitHub Actions 环境变量中安全读取由 Streamlit 传入的数据
SHEET_URL       = os.getenv("SHEET_URL", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

TEMP_DIR = "temp_workspace"


# ==============================================================================
# 🛠️ 1. Google 表格多 Tab 数据解析器
# ==============================================================================

def get_google_sheet_tab_csv(sheet_url, tab_name):
    """提取 Google 表格特定 Tab 名称的 CSV 数据"""
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not match:
        raise ValueError("❌ 提供 Google 表格链接格式不正确！")
    
    sheet_id = match.group(1)
    # 通过 Tab 名称抓取导出 CSV
    encoded_tab = urllib.parse.quote(tab_name)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_tab}"
    
    res = requests.get(export_url, headers={"User-Agent": "Mozilla/5.0"})
    res.encoding = 'utf-8'
    if res.status_code != 200:
        raise RuntimeError(f"❌ 无法读取表格的 [{tab_name}] Tab，请检查 Tab 名称是否正确或权限是否公开！")
    return res.text

def parse_rules_tab(csv_text):
    """解析【映射规则】Tab：提取全局配置与各个样式的配置字典"""
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
    """解析【剧本脚本】Tab：按行提取台词、控制标签与参数"""
    lines = [line.strip() for line in csv_text.strip().splitlines() if line.strip()]
    rows = list(csv.reader(lines))
    if not rows: return []
    
    header = [c.strip() for c in rows[0]]
    clips = []
    
    for row in rows[1:]:
        if not row: continue
        row_dict = {header[i]: row[i].strip() for i in range(len(header)) if i < len(row)}
        
        # 自动跳过带 # 开头的注释行
        text = row_dict.get("台词文本", "").strip()
        if not text or text.startswith("#"): continue
        
        clips.append({
            "type": row_dict.get("类型", "speech").strip(), # speech (口播) 或 card (卡片)
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
# 🔍 2. 素材检索与精准匹配 (零硬编码，纯规则驱动)
# ==============================================================================

def search_pixabay_online_bg(keyword):
    """调用 Pixabay API 搜索高清视频素材 (视频优先)"""
    if not PIXABAY_API_KEY or not keyword: return ""
    
    # 自动将中文关键词翻译为英文（提高匹配率）
    try:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=zh-CN&tl=en&dt=t&q={urllib.parse.quote(keyword)}"
        res = requests.get(url, timeout=3).json()
        en_kw = "".join([item[0] for item in res[0] if item[0]]) or keyword
    except Exception:
        en_kw = keyword

    # 优先检索横屏视频
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
    """背景选择规则：剧本指定文件 > 剧本关键词 > 本地图库匹配 > 全局表格兜底"""
    # 1. 剧本直接指定文件名
    if clip_data["bg_asset"] and os.path.exists(f"materials/{clip_data['bg_asset']}"):
        return f"materials/{clip_data['bg_asset']}"
        
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
# 🎬 3. FFmpeg 音视频剪辑合成引擎
# ==============================================================================

async def generate_audio(text, voice_code, speed_val, output_path):
    """使用 edge-tts 生成高拟真配音"""
    try:
        val = float(str(speed_val).strip())
        pct = round((val - 1.0) * 100)
        rate_str = f"+{pct}%" if pct >= 0 else f"{pct}%"
    except Exception: rate_str = "+0%"
    
    await edge_tts.Communicate(text, voice_code, rate=rate_str).save(output_path)

def render_clip(index, clip, style_cfg, global_cfg, font_path, audio_file, output_path):
    """单个镜头的音画同步剪辑 (关键：-stream_loop 无缝循环，防黑屏)"""
    width, height = 1920, 1080
    fps = global_cfg.get("视频帧率", "30") or "30"
    
    font_size = style_cfg.get("文字字号", "40")
    font_color = style_cfg.get("字体颜色", "white")
    bg_color = style_cfg.get("兜底底色", "black")
    duration = style_cfg.get("停留秒数", "3.0")

    # 字幕遮罩文件
    sub_file = os.path.abspath(f"{TEMP_DIR}/sub_{index}.txt")
    with open(sub_file, "w", encoding="utf-8") as f: f.write(clip["text"])

    cmd = ["ffmpeg", "-y"]
    
    # 获取背景素材
    bg_path = get_clip_background(clip, global_cfg.get("默认素材/图库路径", "materials/bg_lib/"))
    
    if bg_path and os.path.exists(bg_path):
        if bg_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            cmd.extend(["-stream_loop", "-1", "-i", bg_path]) # 视频无限循环，防黑屏
        else:
            cmd.extend(["-loop", "1", "-i", bg_path])        # 图片持续定格
    else:
        cmd.extend(["-f", "lavfi", "-i", f"color=c={bg_color}:s={width}x{height}"])

    # 语音输入
    if audio_file:
        cmd.extend(["-i", audio_file])
    else:
        cmd.extend(["-f", "lavfi", "-i", f"anullsrc=cl=stereo:r=44100:d={duration}"])

    # 滤镜链：拉伸裁剪铺满 + 叠加文字
    filter_chain = (
        f"[0:v]setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}[bg];"
        f"[bg]drawtext=fontfile='{font_path}':textfile='{sub_file}':fontcolor={font_color}:fontsize={font_size}:x=(W-w)/2:y=H-text_h-100[outv]"
    )

    cmd.extend([
        "-filter_complex", filter_chain,
        "-map", "[outv]", "-map", "1:a",
        "-r", str(fps), "-c:v", "libx264", "-c:a", "aac",
        "-shortest", # 以音频长度为基准，精确切断
        output_path
    ])
    
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"❌ 镜头 {index} 渲染失败日志:\n{res.stderr}")
        raise RuntimeError(f"FFmpeg 渲染镜头 {index} 失败")


# ==============================================================================
# 🚀 4. 主程序入口
# ==============================================================================

async def main():
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    print("▶ [1/4] 从 Google 表格读取多 Tab 配置与剧本...")
    rules_csv = get_google_sheet_tab_csv(SHEET_URL, RULES_TAB_NAME)
    script_csv = get_google_sheet_tab_csv(SHEET_URL, SCRIPT_TAB_NAME)
    
    global_cfg, styles_cfg = parse_rules_tab(rules_csv)
    clips = parse_script_tab(script_csv)
    
    font_path = DEFAULT_FONT_PATH if os.path.exists(DEFAULT_FONT_PATH) else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    print(f"▶ [2/4] 解析出 {len(clips)} 个分镜镜头，开始逐个合成...")
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
