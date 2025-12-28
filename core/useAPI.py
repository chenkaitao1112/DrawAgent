import base64
from openai import OpenAI
import json
import re

# 初始化客户端 (针对 Gemini 的 OpenAI 兼容模式)
client = OpenAI(
    api_key="你的_GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)


def encode_image(uploaded_file):
    """将上传的图片文件转换为 base64 字符串"""
    if uploaded_file is not None:
        return base64.b64encode(uploaded_file.read()).decode('utf-8')
    return None


def call_gemini_router(prompt_text, image_base64=None):
    """调用 Gemini API 进行路由判断"""

    # 构造消息内容
    content = [{"type": "text", "text": prompt_text}]

    # 如果有图片，加入消息序列 [cite: 88, 543]
    if image_base64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
        })

    try:
        response = client.chat.completions.create(
            model="gemini-1.5-flash",  # 或 gemini-1.5-pro
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"}  # 强制要求返回 JSON
        )
        return parse_router_response(response.choices[0].message.content)
    except Exception as e:
        return f"Error: {str(e)}"


def parse_router_response(raw_response):
    """
    将 AI 返回的内容解析为 Python 字典。
    加入正则处理，防止 AI 在 JSON 前后加废话。
    """
    try:
        # 使用正则提取最外层的 {} 内容，防止 AI 返回 Markdown 代码块格式
        json_str = re.search(r'\{.*\}', raw_response, re.DOTALL).group()
        res_dict = json.loads(json_str)
        return res_dict
    except Exception as e:
        # 如果解析失败，提供一个兜底方案（默认进入创意流程）
        return {
            "flow": "Creative",
            "picture_label": "None",
            "reason": f"解析失败: {str(e)}"
        }