import streamlit as st
import requests

st.set_page_config(page_title="视频自动化云端控制台", layout="centered")

st.title("🎬 视频自动化云端渲染中心")
st.caption("前端极简交互 + GitHub Actions 后台高能渲染引擎")

# 从 Streamlit Secrets 安全读取凭证 (公开仓库也不怕泄露)
GITHUB_TOKEN = st.secrets.get("GITHUB_TOKEN", "")
GITHUB_REPO = st.secrets.get("GITHUB_REPO", "")

sheet_url = st.text_input("Google Sheet 控制矩阵与剧本链接", placeholder="https://docs.google.com/spreadsheets/d/...")

if st.button("🚀 启动云端视频渲染", type="primary"):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        st.error("❌ 请先在 Streamlit 后台配置 GITHUB_TOKEN 和 GITHUB_REPO 的 Secrets！")
    elif not sheet_url:
        st.error("❌ 请先填写完整的 Google Sheet 链接！")
    else:
        # 触发 GitHub Actions 触发接口
        dispatch_url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/render.yml/dispatches"
        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json"
        }
        payload = {
            "ref": "main",
            "inputs": {
                "sheet_url": sheet_url
            }
        }
        
        res = requests.post(dispatch_url, headers=headers, json=payload)
        
        if res.status_code in [204, 200]:
            st.success("🎉 云端渲染任务已成功触发！")
            st.info(f"👉 请前往 GitHub 查看实时进度并下载成品：https://github.com/{GITHUB_REPO}/actions")
        else:
            st.error(f"❌ 触发失败 (代码 {res.status_code}): {res.text}")
