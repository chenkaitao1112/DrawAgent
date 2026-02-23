import tempfile

import openai
import streamlit as st
import json
import requests
import google.generativeai as genai
from PIL import Image
import io
import re

from openai import OpenAI

# ==========================================
# 1. 配置与初始化
# ==========================================
st.set_page_config(page_title="科研绘图 Agent", layout="wide")
banana_api_key= 
gemini_api_key = 
ONEAPI_BASE_URL = "https://oneapi.gptnb.ai/v1/chat/completions"
# 侧边栏配置
with st.sidebar:
    st.title("⚙️ 设置")
    model_name = st.selectbox("选择模型", ["gemini-3-pro-preview"])
    st.markdown("---")
    st.info("本应用基于《科研绘图.pdf》构建，实现了 Modality-Aware Agentic Workflow。")



if "result_storage" not in st.session_state:
    st.session_state["result_storage"] = {
        "final_prompt": None,
        "image_url": None,
        "xml_code": None,
        "logs": [] # 用于存储思维链日志
    }

if gemini_api_key:
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel(model_name)
    model_instance = genai.GenerativeModel("gemini-3-pro-preview")
else:
    st.warning("请先在左侧输入 Gemini API Key")
    model = None


# 辅助函数：调用 LLM
def call_llm(prompt,  base_url="https://oneapi.gptnb.ai/v1", model="gemini-3-pro-preview", timeout=120):
    """
    融合 SDK 简洁性 + 原生 HTTP 可控性的优化版本
    :param prompt: 提示文本
    :param api_key: oneAPI Key
    :param base_url: 接口基础地址
    :param model: 兼容的模型名（第三方通用）
    :param timeout: 超时时间（默认120秒）
    :return: 回复内容 / 错误信息
    """
    api_key = gemini_api_key

    # 2. 使用 OpenAI SDK 调用（简洁且不易出错）
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=20000,
            timeout=timeout  # 显式设置超时时间
        )
        # 解析结果
        if response.choices:
            content = response.choices[0].message.content.strip()
            print(f"[成功] 模型回复长度：{len(content)}")
            return content
        else:
            return "错误：API 返回结果中无有效内容"
    # 3. 异常处理（覆盖所有场景）

    except Exception as e:
        return f"错误：调用失败 - {str(e)}"


def call_nano_banana(prompt, banana_api_key, model="gemini-3-pro-image-preview", width=1024, height=1024):
    """
    调用第三方 oneapi.gptnb.ai 图片生成接口（严格匹配官方文档，兼容原函数入参/返回值）
    :param prompt: 绘图指令（final_prompt_output）
    :param banana_api_key: 第三方平台的 API Key（在 oneapi.gptnb.ai 申请）
    :param model: 使用的模型，默认 gemini-3-pro-image-preview（可替换为 dall-e-3 等）
    :param width/height: 图片尺寸，默认1024x1024
    :return: 图片URL（成功）/None + 错误信息（失败）
    """
    # 1. 官方图片生成接口地址
    url = "https://oneapi.gptnb.ai/v1/images/generations"

    # 2. 构造请求头（严格匹配官方 Header 要求）
    headers = {
        "Authorization": f"Bearer {banana_api_key}",  # 官方示例的 Bearer 鉴权方式
        "Content-Type": "application/json"
    }

    # 3. 校验官方要求的参数规则（提前拦截错误）
    # 校验尺寸：仅支持256x256/512x512/1024x1024
    valid_sizes = ["256x256", "512x512", "1024x1024"]
    size_str = f"{width}x{height}"
    if size_str not in valid_sizes:
        return None, f"尺寸错误：仅支持 {valid_sizes}，当前传入 {size_str}"

    # 校验prompt长度：官方限制最大1000字符


    # 校验n的范围：官方要求1-10之间
    n = 1  # 保持默认生成1张
    if n < 1 or n > 10:
        return None, f"生成数量错误：n必须介于1-10之间，当前为{n}"

    # 4. 构造请求体（严格匹配官方示例，删除冗余参数）
    data = {
        "prompt": prompt,  # 必需：绘图描述（已截断到1000字符内）
        "n": n,  # 可选：生成数量（默认1）
        "model": model,  # 保留入参的model（兼容原调用逻辑）
        "size": size_str  # 可选：尺寸（仅用官方支持的值）
    }

    try:
        # 5. 发送POST请求（超时保持60秒，适配图片生成耗时）
        response = requests.post(
            url=url,
            headers=headers,
            json=data,
            timeout=60
        )
        response.raise_for_status()  # 捕获4xx/5xx HTTP错误

        # 6. 解析官方返回结果（按OpenAI标准格式）
        result = response.json()
        image_url = result.get("data", [{}])[0].get("url")

        if image_url:
            return image_url, None  # 成功：返回图片URL + 空错误
        else:
            return None, f"未找到图片URL，原始返回：{json.dumps(result, ensure_ascii=False)}"

    # 7. 异常处理（保留原有逻辑，优化提示语）
    except requests.exceptions.Timeout:
        return None, "请求超时：第三方 API 响应超过60秒"
    except requests.exceptions.ConnectionError:
        return None, "连接失败：无法访问 https://oneapi.gptnb.ai"
    except requests.exceptions.HTTPError as e:
        # 优先解析JSON格式的错误详情，更贴合官方返回
        error_detail = ""
        try:
            error_detail = json.dumps(response.json(), ensure_ascii=False)
        except:
            error_detail = response.text
        return None, f"HTTP错误：{str(e)}，详情：{error_detail}"
    except Exception as e:
        return None, f"未知错误：{str(e)}"


def generate_xml_from_image(image_url, client):
    """调用LLM生成XML代码（隐藏所有调用细节）"""
    try:
        # 1. 获取XML生成Prompt
        xml_prompt = get_xml_generation_prompt(image_url)
        # 2. 调用LLM生成XML（此处用模拟结果，实际替换为真实call_llm逻辑）
        # 隐藏所有LLM调用和代码展示，仅返回最终XML
        mock_xml = """<mxGraphModel dx="1000" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1"><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="2" value="溶酶体" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#2E86AB;" vertex="1" parent="1"><mxGeometry x="100" y="200" width="120" height="80" as="geometry"/></mxCell><mxCell id="3" value="线粒体" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#A23B72;" vertex="1" parent="1"><mxGeometry x="300" y="200" width="120" height="80" as="geometry"/></mxCell><mxCell id="4" value="" style="endArrow=block;html=1;strokeColor=#333333;" edge="1" parent="1"><mxGeometry relative="1" as="geometry"/></mxCell></root></mxGraphModel>"""
        return mock_xml, None
    except Exception as e:
        return None, f"XML生成失败：{str(e)}"


def get_ROUTER_PROMPT(orl_text):
    return f"""
# Role: 学术内容意图与领域专家 (Academic Intent & Domain Specialist)
# Profile:
你是一位博学的学术顾问，熟悉理工科（CS/AI、生物、化学、物理）、社科（经济、管理、心理学）及人文艺术等各个学科的研究生细分领域。你的任务是精准识别用户输入的学术内容所属的 具体研究方向。
# Task:
分析用户输入，输出一个严格的 JSON 对象，包含内容类型、一级学科、二级细分领域。
# Classification Rules (分类规则):
1. type: "code" (数据/代码流)
   - 特征：Python/Matlab/R 代码、LaTeX 公式块、原始数据表格、或者明确的“绘制统计图”指令。
   - 目标：识别内容类型，输出类型为"code"。

2. type: "text" (逻辑/文本流)
   - 特征：自然语言描述的实验设计、理论框架、模型架构、概念推导。
   - 目标：识别内容类型，输出类型为"text"。

# Domain Extraction Strategy (领域提取策略 - 必须精确到具体方向):
请通过关键词分析，将领域细化到研究生专业/研究方向级别：
- primary_discipline (一级学科/学院): 如 Computer Science, Economics, Biology, Management.
- specialized_field (具体研究方向): 如 Quantitative Finance (量化金融), Micro-structure (微观结构), Transformer Architecture, Molecular Dynamics (分子动力学).

# Output Format:
仅输出 JSON，不要包含 Markdown 标记。

{{
  "type": "code" | "text",
  "primary_discipline": "String",
  "specialized_field": "String",
}}
# Few-Shot Examples:
## Case 1 (经济学 - 量化):
Input: "假设资产价格服从几何布朗运动 dS = \mu S dt + \sigma S dW，我们需要画出期权定价的模拟路径..."
Output: {{
  "type": "code",
  "primary_discipline": "Economics & Finance",
  "specialized_field": "Quantitative Finance / Stochastic Calculus",
}}

## Case 2 (经济学 - 微观/博弈):
Input: "这是一个双寡头竞争模型，厂商A和厂商B同时决定产量，我们需要画出他们的反应函数曲线和纳什均衡点。"
Output: {{
  "type": "text",
  "primary_discipline": "Economics",
  "specialized_field": "Microeconomics / Game Theory",
}}

## Case 3 (计算机 - Agent):
Input: "设计一个多智能体系统，Agent A负责规划，Agent B负责执行，中间有一个共享记忆模块。"
Output: {{
  "type": "text",
  "primary_discipline": "Computer Science",
  "specialized_field": "Multi-Agent Systems",
}}

## Case 4 (生物学):
Input: "线粒体外膜破裂导致细胞色素c释放，进而激活Caspase级联反应..."
Output: {{
  "type": "text",
  "primary_discipline": "Biology",
  "specialized_field": "Cell Biology / Apoptosis",
}}
##以下为输出{orl_text}
"""


def get_style_extraction_prompt(primary_discipline, specialized_field, conference_name):
    return f"""
    你是一位首席信息设计师，正在分析"{primary_discipline}"的"{specialized_field}中关于"{conference_name}"的视觉风格。
你的任务：
搜索该领域下的该会议或期刊（如果没有提供会议期刊则自由选择该领域）的典型科研图
总结一份视觉设计指南，忽略具体的科学算法内容，仅关注美学和平面设计层面的选择。
关键要求： 不要将每个元素收敛到单一固定的设计选择。相反，请识别每个元素存在哪些常见设计选择，以及哪些更流行或更受青睐。
请重点关注以下具体维度：
1. 色彩方案： 观察配色方案、饱和度水平等。注意令人愉悦的色彩组合，并保留多种选项。
2. 形状与容器： 观察形状选择（如圆角矩形 vs 尖角矩形）、容器、边框（粗细、颜色）、背景填充、阴影等。
3. 线条与箭头： 观察线条粗细、颜色、箭头样式、虚线使用情况。
4. 布局与构图： 观察布局方式、元素排列模式、信息密度、留白使用。
5. 字体与图标： 观察字重、字号、颜色、使用模式以及图标使用方式。
请注意，不同领域的论文可能有不同的美学偏好。例如，智能体（agent）类论文更常使用详细的卡通风格插图，而理论类论文则使用更极简的风格。在总结风格时，请考虑论文的领域。可以使用"对于[领域]，常见选项包括：[列表]"的格式来描述风格。
返回一份简洁的要点式总结，描述这批图表中观察到的视觉风格多样性。
"""

def get_text_logic_prompt(text_content):
    return f"""
#role：你现在的任务是“项目逻辑梳理员”。我将提供我文本的内容。请你严格按照后面的【输出限制】进行回复，不要开始画图，不要讨论视觉风格，只负责梳理逻辑结构。
#Task：从文本中提取以下内容： 1. 核心方法描述 2. 方法结构图对应的目标图表标题 3. 必须在图中出现的关键组件、流程、模块与依赖关系 
仅返回生成准确、完整的方法结构图所必需的核心内容。
#内容如下：
{text_content}
#输出限制 (Constraint)
1.你只对文本内容做总结和归纳，不得修改论文逻辑和内容
2.自动识别重点分配详略
"""

def get_code_logic_prompt(code_content):
    return f"""
#role：你现在的任务是“代码逻辑梳理员”。我将提供我代码的内容。请你严格按照后面的【输出限制】进行回复，不要开始画图，不要讨论视觉风格，只负责梳理逻辑结构。
#Task：从代码中提取以下内容： 
1. 核心逻辑和技术手段描述 2. 模块命名 3. 必须在图中出现的关键组件、流程、模块与依赖关系 
仅返回生成准确、完整的方法结构图所必需的核心内容。
#内容如下：
{code_content}
#输出限制 (Constraint)
1.你只对代码内容做总结和归纳，不得修改论文逻辑和内容
"""


def get_visual_spec_prompt(logic_architecture):
    return f"""
#角色：
你现在的任务是“科研绘图视觉定稿师”。根据用户提供的论文内容梳理，生成一份详尽的、无歧义的“视觉元素规格书”，将抽象的技术描述转换为详细生动的视觉元素
输入 ：逻辑架构
{logic_architecture}
#输出限制：
请仅输出的【视觉元素规格书 (Visual Specification Sheet)】。这是生成最终绘图指令的依据，必须极度具体。

包括： 
1. 所有必要模块、组件、输入、输出、流程的列表 
2. 视觉层级：主要、次要、辅助元素 
3. 组件间的流向与连接关系（数据流、控制流、反馈环） 
4. 排版规则：模块名无衬线字体，数学变量衬线斜体 
5. 使用淡彩分区进行逻辑分组 
6. 自行选择叙事框架（从左到右、从上到下、S型等）最终结构图
7.对复杂的抽象概念使用视觉映射/隐喻（Visual Metaphors）
8.不对具体的绘图风格做约束
"""

def get_final_draw_prompt(logical_context, visual_spec,  style_guideline):
    return f"""
#Role：你是一名专业的科研可视化架构师(Scientific Visualization Architect)。你熟悉各个领域顶会顶刊的科研论文绘图风格。
#task：你现在的任务是将用户提供的“论文逻辑大纲”转化为一段专家级的 AI 绘图提示词 (Image Generation Prompt)。你需要先像“逻辑梳理员”一样审查输入信息的连贯性，然后像“顶级插画师”一样将其转化为视觉语言。
#workflow：
1.读取用户提供的项目逻辑背景和视觉元素设计
2.逻辑审计：检查用户输入的步骤是否有断层、维度是否对齐。
3.视觉元素检查：检查用户视觉元素设计是否合理，是否将抽象技术概念很好的映射为具体元素
4.输出一段符合顶级顶会审美标准的英文 Prompt
输入数据：
##输入 1：项目逻辑
{logical_context}
##输入 2：视觉元素
{visual_spec}

#视觉标准库 (Visual Standards - Strictly Follow)
在生成最终 Prompt 时，必须强制植入以下审美规范：
风格基调：扁平化2D矢量插画，学术风格，简洁干净。禁止3D阴影，禁止照片级写实效果。
{style_guideline}

#生成要求 (Critical Requirements)
1. 格式克隆：不要使用列表（Bullet points），必须是连贯的长段落英文描述。
2. 风格锁定：必须包含 "A professional, scientific diagram in the style of a top-tier conference..." 开头。
3. LaTeX 保留：所有的数学符号（如 $h_t$）必须保留 LaTeX 格式，不要翻译成自然语言。
4. 分块描述：使用 "Section 1 (Left):...", "Section 2 (Middle):..." 这样的结构来引导布局。每个分块用虚线框包围。项目完整背景中每个step就是一个模块一个Section
5. 零废话：直接输出那段英文 Prompt，不要输出任何解释、不要输出中文、不要输出 "Here is your prompt"。
6. 必须包含以下限制
  1. 文字可读性：确保文字占位符具有高对比度。即使文字内容是占位符（无意义字符），其位置安排也必须符合逻辑。
  2. 整洁与对齐：图表必须看起来有条理，不能杂乱。所有模块必须严格对齐到网格系统。
  3. 图例区域：如适用，需包含图例区域，并遵循相应的样式规范。
"""

def get_xml_generation_prompt(picture):
    return f"""
# Role
你是一位 Draw.io（mxGraph）Uncompressed XML 代码生成专家，同时具备精确的图像空间感知能力与颜色辨识能力。你生成的 XML 必须可被 draw.io  直接导入并正确渲染。
# Task
请你深入分析用户上传的流程图图片，并输出一份 Uncompressed mxGraph XML，目标是：
在结构、空间布局与配色方案上，尽可能忠实复刻原图，而非仅表达逻辑关系。
# Critical Rules（必须严格执行）
## 1. 🎨 颜色提取与应用（Color Extraction & Mapping）
你必须从图片中识别不同视觉元素的颜色，并将其转换为 Hex 颜色值（如 #FFC0CB），准确映射到 mxCell 的 style 属性中：
- 容器 / 背景区域
  - 若图片存在彩色背景区域，必须生成一个对应的容器 mxCell
  - 使用准确的 fillColor
  - 必须设置 container="1"
- 流程框 / 节点
  - 若节点在原图中是彩色的，禁止使用白色或默认样式
  - 必须同时设置：
    - fillColor
    - strokeColor
    - fontColor（与背景形成可读对比）
- 边框颜色
  - 明确区分黑色、灰色或彩色边框
  - 禁止统一使用默认黑色
⚠️ 兜底规则（防止模型偷懒）
 若某元素颜色无法精准判断，请选择最接近的中性色（浅灰或深灰），但禁止使用纯白或省略 fillColor。
## 2. 📐 强制绝对坐标估算（Absolute Positioning）
你必须建立图片的心理坐标系并显式估算节点位置：
- 所有节点必须使用绝对坐标
- 每个 vertex 必须包含完整的：
- <mxGeometry x="…" y="…" width="…" height="…" as="geometry"/>
- 禁止将多个节点堆叠在 (0,0)
- 禁止节点之间发生明显重叠
- 整体画布尺寸通常应设为 约 1000 × 1500，以容纳复杂结构
## 3. 🧩 非文本元素的视觉占位符（Visual Placeholders）
若图片中存在非文字图像元素（如卫星图、照片、统计图、Logo 等）：
- 必须生成一个 虚线矩形占位符
- 样式必须严格使用：
- rounded=0;whiteSpace=wrap;html=1;dashed=1;
fillColor=none;strokeColor=#666666;
- value 格式：
- [占位符：元素内容描述]
- 尺寸不得过小（推荐 ≥ 200 × 150）
## 4. 🗂️ 容器结构与层级（Containers & Layering）
- 背景区域必须作为 最底层容器
- 容器规则：
  - vertex="1"
  - container="1"
- 容器必须先于其内部节点出现在 XML 中
- 容器的 parent 必须为主层（见 XML 结构规则）
## 5. 🧱 XML 结构强制约束（非常重要）
你生成的 XML 必须且只能遵循以下基础结构：
<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><!-- 所有其他 mxCell 从 id=2 开始 -->
  </root>
</mxGraphModel>
- 所有可见节点、容器、占位符：
  - 必须 parent="1" 或 parent 为某个容器节点
- mxCell 的 id 必须为递增整数（2, 3, 4, …），禁止重复
## 6. 🧾 Vertex / Edge 基本规范
- 所有可见元素必须使用：
  - vertex="1"
- 若存在连线：
  - 使用 edge="1"
  - 不允许省略 source / target
- 禁止生成与图片无关的多余元素
# Output Format
- 仅输出 XML 代码
- 禁止任何解释性文字
- 禁止 Markdown 包裹
- 禁止压缩格式（如 <mxfile> base64）
# Action
现在，请开始分析图片的空间布局与颜色方案，并生成一份可直接导入 draw.io 的 Uncompressed mxGraph XML。
{picture}
"""


def generate_xml_from_image_real(image_url, model):
    """
    真正的逆向工程：下载图片 -> 转为对象 -> 传给 Vision Model -> 获取 XML
    """
    if not image_url: return None, "错误：图片地址为空"
    if not model: return None, "错误：模型未初始化"

    try:
        # 1. 下载图片 (关键步骤)
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        # 2. 转换为 PIL Image 对象
        img_object = Image.open(io.BytesIO(response.content))

        # 3. 准备 Prompt (传入空串，只取 System Prompt)
        # 注意：这里调用的是 get_xml_generation_prompt
        text_prompt = get_xml_generation_prompt("")

        # 4. 多模态调用 (关键：传入 [文本, 图片对象] 列表)
        ai_response = model.generate_content([text_prompt, img_object])

        # 5. 清洗 Markdown
        clean_xml = ai_response.text.replace("```xml", "").replace("```", "").strip()
        return clean_xml, None

    except Exception as e:
        return None, f"XML 生成失败: {str(e)}"

st.title("🔬 全自动科研绘图 Agent")
st.markdown("Text -> Logic -> Visual Spec -> **Image** -> **XML Code**")

col_left, col_right = st.columns([1, 1])

with col_left:
    user_input = st.text_area("输入论文摘要、代码或实验设计", height=200,
                              placeholder="例如：设计一个 Transformer 架构图...")
    target_conf = st.text_input("目标期刊/会议 (例如 CVPR, Nature)", value="Top-tier Conference")

    start_btn = st.button("🚀 开始全流程生成", type="primary")

    # ================= 核心工作流逻辑 =================
    if start_btn and user_input:
        if not banana_api_key or not model:
            st.error("请先配置 API Key")
        else:
            # 清空旧结果
            st.session_state["result_storage"] = {"final_prompt": None, "image_url": None, "xml_code": None, "logs": []}

            logs = []
            status_container = st.status("正在执行 Agentic Workflow...", expanded=True)

            try:
                # --- Step 1: Router (智能路由) ---
                status_container.write("🔄 Step 1: 识别领域与类型...")
                router_res = call_llm(get_ROUTER_PROMPT(user_input))
                logs.append(f"**Router Output:**\n{router_res}")

                # 解析 JSON
                try:
                    clean_json = router_res.replace("```json", "").replace("```", "").strip()
                    router_data = json.loads(clean_json)
                    input_type = router_data.get("type", "text")
                    p_disc = router_data.get("primary_discipline", "Science")
                    s_field = router_data.get("specialized_field", "General")
                except:
                    input_type, p_disc, s_field = "text", "Science", "General"

                # --- Step 2: Style Extraction (风格提取) ---
                status_container.write(f"🎨 Step 2: 提取 {target_conf} 视觉风格...")
                style_res = call_llm(get_style_extraction_prompt(p_disc, s_field, target_conf))
                logs.append(f"**Style Guide:**\n{style_res}")

                # --- Step 3: Logic Extraction (逻辑梳理) ---
                status_container.write("🧠 Step 3: 梳理核心逻辑...")
                if input_type == "code":
                    logic_prompt = get_code_logic_prompt(user_input)
                else:
                    logic_prompt = get_text_logic_prompt(user_input)
                logic_res = call_llm(logic_prompt)
                logs.append(f"**Logic Structure:**\n{logic_res}")

                # --- Step 4: Visual Specs (视觉规格) ---
                status_container.write("📐 Step 4: 生成视觉规格书...")
                visual_res = call_llm(get_visual_spec_prompt(logic_res))
                logs.append(f"**Visual Specs:**\n{visual_res}")

                # --- Step 5: Final Prompt (生成绘图指令) ---
                status_container.write("✍️ Step 5: 编写最终绘图 Prompt...")
                final_draw_instruction = get_final_draw_prompt(logic_res, visual_res, style_res)
                final_prompt = call_llm(final_draw_instruction)
                logs.append(f"**Final Prompt:**\n{final_prompt}")

                st.session_state["result_storage"]["final_prompt"] = final_prompt

                # --- Step 6: Image Generation (调用生图) ---
                status_container.write("🖼️ Step 6: AI 正在绘图 (Nano banana)...")
                image_url, img_err = call_nano_banana(final_prompt, banana_api_key)

                if img_err:
                    st.error(f"绘图失败: {img_err}")
                else:
                    st.session_state["result_storage"]["image_url"] = image_url

                    # --- Step 7: XML Reverse Engineering (逆向工程) ---
                    status_container.write("🧱 Step 7: 正在逆向生成 Draw.io XML...")
                    xml_code, xml_err = generate_xml_from_image_real(image_url, model)

                    if xml_code:
                        # 清洗 Markdown 标记
                        clean_xml = re.sub(r"```xml|```", "", xml_code).strip()
                        st.session_state["result_storage"]["xml_code"] = clean_xml
                    else:
                        st.warning(f"XML 生成异常: {xml_err}")

                st.session_state["result_storage"]["logs"] = logs
                status_container.update(label="✅ 全流程执行完毕", state="complete", expanded=False)

                # 强制刷新以显示结果
                st.rerun()

            except Exception as e:
                st.error(f"工作流出错: {str(e)}")

with col_right:
    # 从 Session State 读取结果 (保证交互不丢失)
    res = st.session_state["result_storage"]

    if res["image_url"]:
        st.subheader("1. AI 生成的图表")
        st.image(res["image_url"], caption="基于 Agentic Workflow 生成", use_container_width=True)

        with st.expander("查看中间过程 (思维链日志)"):
            for log in res["logs"]:
                st.markdown(log)
                st.divider()

    if res["xml_code"]:
        st.subheader("2. Draw.io XML 代码")
        st.info("复制 -> 打开 draw.io -> Extras -> Edit Diagram -> 粘贴")
        st.code(res["xml_code"], language="xml")

    elif not res["image_url"]:
        st.info("👈 等待输入并启动...")
        st.markdown("""
        **执行逻辑：**
        1. Router (识别类型)
        2. Style Extraction (风格定义)
        3. Logic Extraction (逻辑抽象)
        4. Visual Spec (视觉映射)
        5. Final Prompt (指令生成)
        6. Image Gen (绘图)
        7. Vision to XML (逆向代码)
        """)
