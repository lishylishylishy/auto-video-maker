import streamlit as st
import requests, json, time, os, zipfile, io
import gspread
from google.oauth2.service_account import Credentials

# ==============================================================================
# ⚙️ 【一、 全局配置与密钥集中管理区】(修改参数请在此处统一调整)
# ==============================================================================

# 1. Google Sheets 表格编辑与内嵌预览链接
# 备注：请确保你的 Google 服务账号（Service Account）已获得该表格的编辑权限
SHEET_EDIT_URL  = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"
SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/htmlview"

# 2. 默认写入与渲染的 Google Sheets 底栏 Tab 名称（按你的要求固定为 scripts）
DEFAULT_TAB_NAME = "scripts"

# 3. Gemini 文本 API 调用模型版本
GEMINI_MODEL_NAME = "gemini-2.5-flash"

# 4. 自动化视频脚本的标准表头（固定 8 列结构）
SCRIPT_SHEET_HEADERS = [
    "章节", 
    "章节秒数", 
    "无声文字", 
    "有声文字", 
    "默认素材-layer0", 
    "挂件素材-layer1", 
    "配音-音色代号", 
    "配音-语速"
]

# 5. 安全读取 Secret 配置工具函数（优先从 st.secrets 读取，环境变量兜底）
def get_secret(key, default=""):
    """
    【配置说明】：
    请在 Streamlit Cloud 的 Settings -> Secrets 中配置以下键值对：
    - GEMINI_API_KEY      : Google AI Studio 获得的 API Key
    - GCP_SA_KEY          : GCP 服务账号 JSON 密钥字符串（用于读写 Google Sheets）
    - GITHUB_TOKEN        : 用于触发 Actions 渲染的 GitHub 个人令牌
    - GITHUB_REPOSITORY   : 格式为 "用户名/仓库名"
    - GITHUB_REF          : 默认 "main"
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


# ==============================================================================
# 🎨 【二、 Streamlit 页面初始化与 CSS 样式修复】
# ==============================================================================

st.set_page_config(
    page_title="Auto Video Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 🌟 CSS 样式修饰：隐藏右侧杂项菜单，并【核心修复】侧边栏展开按钮丢失的问题
st.markdown("""
    <style>
        /* 1. 隐藏右侧 Streamlit 默认三点菜单与 Bottom Footer */
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        
        /* 2. 将顶部 Header 设为透明背景，防止阻挡页面点击 */
        header[data-testid="stHeader"] {
            background-color: transparent !important;
            z-index: 100 !important;
        }
        
        /* 3. 核心修复：强行显示侧边栏展开/折叠按钮 (>> / <<)，确保关闭后能重新打开 */
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarHeader"] button,
        button[aria-label="Expand sidebar"],
        button[aria-label="Close sidebar"] {
            display: flex !important;
            visibility: visible !important;
            z-index: 999999 !important;
            background-color: #ffffff !important;
            border: 1px solid #ddd !important;
            border-radius: 4px !important;
        }

        /* 4. 调整主界面边距与内嵌表格样式 */
        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 1rem !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }
        iframe {
            border-radius: 8px;
            border: 1px solid #e0e0e0;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }
    </style>
""", unsafe_allow_html=True)


# ==============================================================================
# 🤖 【三、 AI 文本转固定脚本 & Google Sheets 读写逻辑】
# ==============================================================================

def generate_structured_script_via_gemini(raw_text):
    """
    调用 Google Gemini API 将自由文本草稿格式化为严格的 8 列 JSON 分镜数组
    """
    gemini_key = get_secret("GEMINI_API_KEY", "")
    if not gemini_key:
        raise ValueError("未在 Streamlit Secrets 中找到 GEMINI_API_KEY，请先添加！")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_NAME}:generateContent?key={gemini_key}"
    
    # 构造强约束 Prompt
    prompt = f"""
你是一位专业短视频导演和剪辑师。请将输入的文案草稿整理并拆解为标准的自动化视频脚本分镜表。

【格式强制要求】：
必须严格返回一个 JSON 数组，数组中的每个对象必须且只能包含以下 8 个字段：
1. "章节": 只能在 ["片头", "转场", "结尾", "男配音", "女配音"] 中选择。
2. "章节秒数": 预估当前片段秒数（如 "3" 或 "5"；若有配音填空字符串 ""）。
3. "无声文字": 屏幕画面出现的字幕/大标题（若无填空字符串 ""）。
4. "有声文字": 需要朗读的配音台词（若无填空字符串 ""）。
5. "默认素材-layer0": 提炼适合搜索画面视频/图片的英文关键词（如 "cyberpunk city"；若无填空字符串 ""）。
6. "挂件素材-layer1": 填空字符串 ""。
7. "配音-音色代号": 默认填 "zh-CN-YunxiNeural"。
8. "配音-语速": 默认填 "1.0"。

【草稿文案内容】：
{raw_text}
"""
    headers = {"Content-Type": "application/json"}
    
    # 开启 response_mime_type: application/json 强制返回 JSON 格式
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=40)
    if res.status_code != 200:
        raise RuntimeError(f"Gemini API 请求失败 [{res.status_code}]: {res.text}")
        
    result_json = res.json()
    text_content = result_json['candidates'][0]['content']['parts'][0]['text']
    
    # 验证并解析 JSON 数据
    parsed_data = json.loads(text_content)
    if not isinstance(parsed_data, list):
        raise ValueError("Gemini 返回的数据格式有误，未返回 JSON 列表。")
    return parsed_data


def write_script_to_google_sheet(sheet_url, tab_name, script_data):
    """
    通过 Google Service Account API 操作 Google Sheets：
    1. 尝试寻找名为 tab_name (默认 'scripts') 的页签；如果不存在则新建。
    2. 清空该页签原有内容。
    3. 写入标准 8 列表头以及 AI 生成的表格内容。
    """
    gcp_key_str = get_secret("GCP_SA_KEY", "")
    if not gcp_key_str:
        raise ValueError("未在 Streamlit Secrets 中找到 GCP_SA_KEY 密钥！")

    try:
        service_account_info = json.loads(gcp_key_str)
    except Exception:
        raise ValueError("GCP_SA_KEY 格式有误，必须是完整的 JSON 字符串！")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)
    
    # 打开指定 URL 的 Sheet 文件
    sh = gc.open_by_url(sheet_url)
    
    # 检查 Tab 是否存在，不存在则新建 tab_name (scripts)
    try:
        worksheet = sh.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=tab_name, rows="100", cols="20")
        
    # 组装完整的二维表格数据矩阵
    rows_to_write = [SCRIPT_SHEET_HEADERS]
    for item in script_data:
        row = [
            str(item.get("章节", "")),
            str(item.get("章节秒数", "")),
            str(item.get("无声文字", "")),
            str(item.get("有声文字", "")),
            str(item.get("默认素材-layer0", "")),
            str(item.get("挂件素材-layer1", "")),
            str(item.get("配音-音色代号", "zh-CN-YunxiNeural")),
            str(item.get("配音-语速", "1.0"))
        ]
        rows_to_write.append(row)
        
    # 清空旧内容，并从 A1 开始全量覆盖更新
    worksheet.clear()
    worksheet.update(range_name='A1', values=rows_to_write)


# ==============================================================================
# 🚀 【四、 GitHub Actions 渲染触发与状态查询】(保留原有稳定代码)
# ==============================================================================

def trigger_github_workflow(tab_name, resolution):
    """ 请求 GitHub API 触发 Action 自动化云端渲染工作流 """
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    ref = get_secret("GITHUB_REF", "main")

    if not token or not repo or repo == "your_username/auto-video-maker":
        return False, "请检查 Streamlit Secrets 中的 GITHUB_TOKEN 与 GITHUB_REPOSITORY。"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/render.yml/dispatches"
    payload = {"ref": ref, "inputs": {"sheet_tab": tab_name, "resolution": resolution}}
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 204:
            return True, "成功"
        else:
            return False, f"GitHub 返回 [{res.status_code}]: {res.text}"
    except Exception as e:
        return False, f"网络请求异常: {str(e)}"


def get_latest_workflow_run():
    """ 查询 GitHub Actions 最新一次渲染任务的运行状态 """
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    
    if not token or not repo or repo == "your_username/auto-video-maker":
        return None
        
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=1"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("workflow_runs"):
                return data["workflow_runs"][0]
    except Exception:
        pass
    return None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_rendered_video_bytes(run_id):
    """ 从 GitHub API 产物 Artifacts 下载 zip 并在内存中解压提取最终生成的 MP4 """
    token = get_secret("GITHUB_TOKEN", "")
    repo = get_secret("GITHUB_REPOSITORY", get_secret("GITHUB_REPO", ""))
    
    if not token or not repo:
        return None
        
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}"
    }
    
    artifacts_url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts"
    try:
        res = requests.get(artifacts_url, headers=headers, timeout=10)
        if res.status_code != 200:
            return None
            
        artifacts = res.json().get("artifacts", [])
        target_art = None
        for art in artifacts:
            if art.get("name") == "Rendered-Video-Output":
                target_art = art
                break
                
        if not target_art:
            return None
            
        download_url = target_art.get("archive_download_url")
        zip_res = requests.get(download_url, headers=headers, timeout=30)
        if zip_res.status_code != 200:
            return None
            
        with zipfile.ZipFile(io.BytesIO(zip_res.content)) as z:
            for filename in z.namelist():
                if filename.endswith(".mp4"):
                    return z.read(filename)
    except Exception as e:
        print(f"提取视频失败: {e}")
        
    return None


# ==============================================================================
# 📌 【五、 侧边控制面板 UI 界面构建】
# ==============================================================================

with st.sidebar:
    st.title("🎬 Auto-Studio")
    st.markdown("---")
    
    # 渲染目标 Tab，默认填充全局配置的 scripts
    target_tab = st.text_input("Target Sheet Tab", value=DEFAULT_TAB_NAME, help="输入 Google Sheet 底部的 Tab 名称")
    video_resolution = st.text_input(
        "视频分辨率",
        value="1920x1080",
        help="格式：宽x高，例如 1920x1080、1280x720"
    )
    
    # 点击按钮触发 Actions 渲染
    if st.button("🚀 Start Rendering", type="primary", use_container_width=True):
        ok, msg = trigger_github_workflow(target_tab, video_resolution)
        if ok:
            st.cache_data.clear() # 清理旧视频缓存
            st.success("✅ 云端渲染任务已启动！您可以随时关闭浏览器，后台会自动运行。")
            time.sleep(1)
            st.rerun()
        else:
            st.error(f"❌ 启动失败：{msg}")

    st.markdown("---")
    st.subheader("📡 Process Status")
    
    # 实时轮询 GitHub Actions 渲染进度
    run_info = get_latest_workflow_run()
    if run_info:
        status = run_info.get("status")
        conclusion = run_info.get("conclusion")
        run_id = run_info.get("id")
        
        if status in ["in_progress", "queued"]:
            st.info(f"⏳ **云端渲染中...** ({status})")
            st.progress(50)
            st.caption("您可以关闭此页面，渲染完成后重新打开即可预览视频。")
            if st.button("🔄 刷新进度", use_container_width=True):
                st.rerun()
        elif status == "completed":
            if conclusion == "success":
                st.success("🎉 **视频渲染成功！**")
                
                # 自动拉取视频并显示在侧边栏
                with st.spinner("📥 正在加载视频预览窗口..."):
                    video_bytes = fetch_rendered_video_bytes(run_id)
                    
                if video_bytes:
                    st.video(video_bytes)
                    st.download_button(
                        label="⬇️ 下载视频",
                        data=video_bytes,
                        file_name="final_output.mp4",
                        mime="video/mp4",
                        type="primary",
                        use_container_width=True
                    )
                else:
                    st.warning("⚠️ 视频文件提取中，请稍等片刻后刷新。")
                    if st.button("🔄 重新拉取视频", use_container_width=True):
                        st.cache_data.clear()
                        st.rerun()
            else:
                st.error(f"❌ 渲染失败，结论: {conclusion}")
                st.markdown(f"[🔍 点击查看 GitHub 错误日志]({run_info.get('html_url')})")
    else:
        st.caption("暂无云端运行记录。")


# ==============================================================================
# 📊 【六、 主界面 UI：AI 智能剧本生成器 + 表格预览】
# ==============================================================================

# 顶部展开模块：AI 文本转剧本
with st.expander("🪄 **AI 剧本智能生成器（文本草稿 ➔ 自动新建 scripts 表格）**", expanded=True):
    raw_draft = st.text_area(
        "粘贴你的原始文章 / 文案草稿：",
        placeholder="例如：很久很久以前，有一只聪明的狐狸住在森林里...",
        height=120
    )
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        # 默认写入名为 scripts 的 Tab
        write_target_tab = st.text_input("写入的目标 Tab 页名称：", value=DEFAULT_TAB_NAME)
    with col_b:
        st.write("")
        st.write("")
        gen_btn = st.button("✨ 一键生成并写入 Google 表格", type="primary", use_container_width=True)
        
    if gen_btn:
        if not raw_draft.strip():
            st.warning("⚠️ 请先粘贴文案草稿！")
        else:
            with st.spinner("🤖 正在调用 Gemini 拆解分镜并自动新建/更新表格..."):
                try:
                    # 1. 调用 Gemini 解析生成 JSON 列表
                    script_data = generate_structured_script_via_gemini(raw_draft)
                    
                    # 2. 写入 Google Sheets (自动建立 scripts 页)
                    write_script_to_google_sheet(SHEET_EDIT_URL, write_target_tab, script_data)
                    
                    st.success(f"🎉 剧本解析成功！已自动新建并写入 Google 表格【{write_target_tab}】页中。")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 生成或写入失败: {str(e)}")

st.divider()

# 下方主视窗：嵌入 Google 表格在线编辑器
col_title, col_btn = st.columns([3, 1], vertical_alignment="center")
with col_title:
    st.subheader("📋 Google Sheets 内容在线编辑视窗")
with col_btn:
    st.link_button("🔗 在新标签页打开表格", SHEET_EDIT_URL, use_container_width=True)

# 嵌入 iframe 视窗
st.components.v1.iframe(SHEET_EMBED_URL, height=520, scrolling=True)
