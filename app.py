import streamlit as st
import requests, json, time, os, zipfile, io
import gspread
from google.oauth2.service_account import Credentials

# ==============================================================================
# ⚙️ 【一、 全局配置与密钥集中管理区】
# ==============================================================================

# 1. Google Sheets 编辑与预览链接
SHEET_EDIT_URL  = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/edit"
SHEET_EMBED_URL = "https://docs.google.com/spreadsheets/d/1Y8YOEl5G2HV4C3WLZz9ttEEoByuWKGLqh_nmWvcb-_M/htmlview"

# 2. 默认写入的目标 Tab 名称（按要求修改为 script）
DEFAULT_TAB_NAME = "script"

# 3. 自动化视频脚本的标准表头（ 6 列）
SCRIPT_SHEET_HEADERS = [
    "章节", 
    "默认素材-layer0", 
    "挂件素材-layer1", 
    "章节秒数", 
    "无声文字", 
    "有声文字"
]

# 4. 可用文本模型列表与备选配置（按 RPD 额度从高到低、性能从好到差排列）
RECOMMENDED_MODELS = {
    "gemini-3.5-flash-lite": "⚡ gemini-3.5-flash-lite (推荐！RPD: 500次/天, RPM: 15)",
    "gemini-3.1-flash-lite": "⚡ gemini-3.1-flash-lite (额度大！RPD: 500次/天, RPM: 15)",
    "gemini-3.8-flash": "🧠 gemini-3.8-flash (最新性能王！RPD: 20次/天, RPM: 5)",
    "gemini-3.6-flash": "🧠 gemini-3.6-flash (高智能！RPD: 20次/天, RPM: 5)",
    "gemini-3-flash": "🛡️ gemini-3-flash (3代备用！RPD: 20次/天, RPM: 5)",
    "gemini-2.5-flash-lite": "🛡️ gemini-2.5-flash-lite (2.5轻量！RPD: 20次/天, RPM: 10)",
    "gemini-2.5-flash": "🛡️ gemini-2.5-flash (2.5经典！RPD: 20次/天, RPM: 5)"
}

# 5. 安全读取 Secrets
def get_secret(key, default=""):
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

# 🌟 CSS 样式修饰：隐藏右侧杂项菜单，强制置顶显示侧边栏展开/折叠按钮
st.markdown("""
    <style>
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        
        header[data-testid="stHeader"] {
            background-color: transparent !important;
            z-index: 100 !important;
        }
        
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
# 🤖 【三、 AI 文本转固定脚本 & Google Sheets 动态读写】
# ==============================================================================

def generate_structured_script_via_gemini(raw_text, model_name="gemini-3.5-flash-lite"):
    """
    调用 Google Gemini API 将自由文本草稿格式化为严格的 6 列 JSON 分镜数组
    """
    gemini_key = get_secret("GEMINI_API_KEY", "")
    if not gemini_key:
        raise ValueError("未在 Streamlit Secrets 中找到 GEMINI_API_KEY，请先添加！")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
    
    # 根据 6 列表头的定制 Prompt
    prompt = f"""
你是一位专业短视频导演和剪辑师。请将输入的文案草稿整理并拆解为标准的自动化视频脚本，首先请对草稿进行分析，提炼故事并进行逻辑拆分，组织成3个或以上逻辑渐进的叙述内容。举例来说，首先的内容是一个现实问题、现象、矛盾，然后的内容是具体的分析解释，再然后的内容是具体的结论论述，最后是衍生思考。每一个内容都以男配音为主要叙事者，负责解释问题并引导整个叙事走向，女配音作为音频点缀，提出一些大众疑问，男配音、女配音的多轮对话的“有声文字”是叙述内容的主体；每一个内容之间用转场衔接，注意，片头、转场、结尾应承上启下，提炼一句话作为“无声文字”的演示大标题hook。具体各章节填写按照故事拆分结果和【格式强制要求】执行。

【格式强制要求】：
必须严格返回一个 JSON 数组，数组包括5种章节，6个字段。
1."章节": 有且只有5种，分别为"片头"、“结尾”、"转场"、"男配音"、"女配音"。"片头"、“结尾”章节分别在首尾各一个，"转场"、"男配音"、"女配音"章节一般都有多个。各个章节按叙述逻辑渐进的顺序排列，一般以“片头”开始，然后是男配音、女配音的多轮对话，然后以“转场”分隔，再接男配音、女配音的对话，对话与转场穿插，最后以“结尾”结束。
1."有声文字": 多轮对话的配音台词。只有"男配音", "女配音"两个章节有"有声文字"，其他章节的"有声文字"为空，"有声文字"以多轮对话的形式完整的讲述整个故事内容，男配音为主要叙事者，女配音为整个故事增加视角。
2."无声文字": 言简意赅的大标题。“片头”的"无声文字"应是全文的hook；"男配音"和"女配音"的"无声文字"一般为空，只在涉及关键点时出现一个词；"转场"的"无声文字"一般是下一个叙述内容的总结、预告；“结尾”的"无声文字"一般是“打开更多脑洞”。
3."章节秒数": 一般为“3”，具体根据“无声文字”的文字长度适当调整，考虑观者识别文字内容的时长。"无声文字"为空的章节，"章节秒数"也可以为空。
4."默认素材-layer0": 只能是一个关键词，例如“城市交通”。提炼对应章节的“有声文字”的关键词用于pixabay平台检索视频素材。
5."挂件素材-layer1": 一般留空。只有当某章节的“有声内容”出现极关键的、具体的要点时，才给出一个要点的“关键词”，用于pixabay平台检索对应的视频素材，例如“USA”。

【草稿文案内容】：
{raw_text}
"""
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    
    res = requests.post(url, headers=headers, json=payload, timeout=40)
    if res.status_code != 200:
        raise RuntimeError(f"模型 [{model_name}] 请求失败 [{res.status_code}]: {res.text}")
        
    result_json = res.json()
    text_content = result_json['candidates'][0]['content']['parts'][0]['text']
    
    parsed_data = json.loads(text_content)
    if not isinstance(parsed_data, list):
        raise ValueError("Gemini 返回的数据格式有误，未返回 JSON 列表。")
    return parsed_data


def write_script_to_google_sheet(sheet_url, tab_name, script_data):
    """
    通过 Google Service Account API 自动创建/清空指定 Tab，并按表头顺序写入数据
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
    
    sh = gc.open_by_url(sheet_url)
    
    # 检查 Tab (script) 是否存在，不存在则新建
    try:
        worksheet = sh.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=tab_name, rows="100", cols="20")
        
    # 🌟 彻底动态化拼装：严格按 SCRIPT_SHEET_HEADERS 顺序导出数据
    rows_to_write = [SCRIPT_SHEET_HEADERS]
    for item in script_data:
        row = [str(item.get(col, "")) for col in SCRIPT_SHEET_HEADERS]
        rows_to_write.append(row)
        
    # 清空旧内容，覆盖更新
    worksheet.clear()
    worksheet.update(range_name='A1', values=rows_to_write)


# ==============================================================================
# 🚀 【四、 GitHub Actions 渲染触发与状态查询】(保留原有稳定代码)
# ==============================================================================

def trigger_github_workflow(tab_name, resolution):
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
    
    target_tab = st.text_input("Target Sheet Tab", value=DEFAULT_TAB_NAME, help="输入 Google Sheet 底部的 Tab 名称")
    video_resolution = st.text_input(
        "视频分辨率",
        value="1920x1080",
        help="格式：宽x高，例如 1920x1080、1280x720"
    )
    
    if st.button("🚀 Start Rendering", type="primary", use_container_width=True):
        ok, msg = trigger_github_workflow(target_tab, video_resolution)
        if ok:
            st.cache_data.clear()
            st.success("✅ 云端渲染任务已启动！您可以随时关闭浏览器，后台会自动运行。")
            time.sleep(1)
            st.rerun()
        else:
            st.error(f"❌ 启动失败：{msg}")

    st.markdown("---")
    st.subheader("📡 Process Status")
    
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

# 顶部模块：AI 剧本生成
with st.expander("🪄 **AI 剧本智能生成器（文本草稿 ➔ 自动新建 script 表格）**", expanded=True):
    raw_draft = st.text_area(
        "粘贴你的原始文章 / 文案草稿：",
        placeholder="例如：有一个狐狸在森林中游玩，他走啊走，来到一个小池塘，这里一只青蛙趴在荷叶上休息...",
        height=120
    )
    
    col_1, col_2, col_3 = st.columns([2, 1, 1])
    
    with col_1:
        # 下拉菜单选择模型，默认推荐 gemini-3.5-flash-lite
        selected_model_key = st.selectbox(
            "选择 Gemini 文本模型（按额度/性能排序）：",
            options=list(RECOMMENDED_MODELS.keys()),
            format_func=lambda x: RECOMMENDED_MODELS[x],
            index=0,
            help="如果某个模型配额用尽报错，请切换列表中的其他备用模型。"
        )
        
    with col_2:
        write_target_tab = st.text_input("写入的目标 Tab 名称：", value=DEFAULT_TAB_NAME)
        
    with col_3:
        st.write("")
        st.write("")
        gen_btn = st.button("✨ 一键生成并写入", type="primary", use_container_width=True)
        
    if gen_btn:
        if not raw_draft.strip():
            st.warning("⚠️ 请先粘贴文案草稿！")
        else:
            with st.spinner(f"🤖 正在调用 [{selected_model_key}] 拆解分镜并写入表格..."):
                try:
                    # 1. 传入选中的模型名称调用 API
                    script_data = generate_structured_script_via_gemini(raw_draft, model_name=selected_model_key)
                    
                    # 2. 写入 Google Sheets (新建/更新 script 页)
                    write_script_to_google_sheet(SHEET_EDIT_URL, write_target_tab, script_data)
                    
                    st.success(f"🎉 剧本解析成功！已使用 [{selected_model_key}] 写入 Google 表格【{write_target_tab}】页中。")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 生成或写入失败: {str(e)}")

st.divider()

# 主视窗：嵌入 Google 表格在线编辑器
col_btn = st.columns([3, 1], vertical_alignment="center")
#with col_title:
#    st.subheader("📋 Google Sheets 内容在线编辑视窗")
with col_btn:
    st.link_button("📋Google Sheets", SHEET_EDIT_URL, use_container_width=True)

st.components.v1.iframe(SHEET_EMBED_URL, height=520, scrolling=True)
